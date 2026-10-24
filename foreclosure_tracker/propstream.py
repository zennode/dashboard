"""Mortgage details from a PropStream export pasted into the sheet.

PropStream has no API, so the loop is:
  1. The tracker writes the addresses that still need mortgage data to the
     "Auto - PropStream Lookup" tab.
  2. Someone imports that list into PropStream and exports it with the mortgage columns.
  3. They paste the export into the "PropStream Export" tab (headers in row 1).
  4. Every run, the tracker reads that tab and fills Mortgage Amount, Mortgage Year,
     Lender, 1st or 2nd and Est. Equity.

PropStream's column names vary by export template, so columns are found by name.
"""

import re
from datetime import datetime

import parse as P

LOOKUP_COLS = ["Address", "City", "State", "Zip", "Auction Date", "Key"]


def _find(header, *patterns, exclude=("mail", "owner")):
    """First column whose lowercased name contains every word of a pattern."""
    for pat in patterns:
        words = pat.split()
        for h in header:
            low = h.lower()
            if all(w in low for w in words) and not any(x in low for x in exclude):
                return h
    return None


def _columns(header):
    loan = {}
    for n, tags in ((1, ("1", "1st", "first")), (2, ("2", "2nd", "second"))):
        pats = lambda what: [f"{what} {t}" for t in tags] + [f"{t} {what}" for t in tags]
        loan[n] = {
            "amount": _find(header, *[p for w in ("mortgage amount", "loan amount") for p in pats(w)],
                            *pats("mortgage amt")),
            "date": _find(header, *[p for w in ("mortgage recording date", "loan recording date",
                                                "mortgage date", "loan date", "recording date") for p in pats(w)]),
            "lender": _find(header, *[p for w in ("mortgage lender", "loan lender", "lender") for p in pats(w)]),
        }
    # Exports with a single loan use plain names.
    plain = ("mail", "owner", "2", "second")
    loan[1]["amount"] = loan[1]["amount"] or _find(header, "mortgage amount", "loan amount", "mortgage amt",
                                                   exclude=plain)
    loan[1]["date"] = loan[1]["date"] or _find(header, "mortgage recording date", "loan recording date",
                                               "mortgage date", "loan date", "recording date", exclude=plain)
    loan[1]["lender"] = loan[1]["lender"] or _find(header, "lender", exclude=plain)
    return {
        "address": _find(header, "property address", "address", exclude=("mail", "owner", "city", "zip", "state")),
        "city": _find(header, "property city", "city", exclude=("mail", "owner")),
        "equity": _find(header, "est equity", "est. equity", "estimated equity", "equity"),
        "balance": _find(header, "open mortgage balance", "est. remaining balance", "est remaining balance",
                         "total open loans", "loan balance"),
        "loans": loan,
    }


def _year(value):
    m = re.search(r"(19|20)\d\d", str(value or ""))
    return m.group(0) if m else ""


def _money(value):
    v = P.parse_money(str(value)) if "$" in str(value) else None
    if v is None:
        try:
            v = float(str(value).replace(",", "").strip())
        except ValueError:
            return ""
    return P.fmt_money(v) if v else ""


def index(export_rows):
    """{address key: details} from the pasted export."""
    if not export_rows:
        return {}
    cols = _columns(list(dict.fromkeys(k for r in export_rows for k in r)))
    if not cols["address"]:
        return {}
    out = {}
    for r in export_rows:
        addr = str(r.get(cols["address"]) or "")
        city = str(r.get(cols["city"]) or "") if cols["city"] else ""
        if not addr:
            continue
        # Some exports put "12 Main St, Springfield, MA 01105" in one cell.
        if not city and "," in addr:
            addr, city = [x.strip() for x in addr.split(",")[:2]]
        loans = []
        for n in (1, 2):
            c = cols["loans"][n]
            amount = _money(r.get(c["amount"])) if c["amount"] else ""
            year = _year(r.get(c["date"])) if c["date"] else ""
            lender = P.clean(str(r.get(c["lender"]) or "")) if c["lender"] else ""
            if amount or year or lender:
                loans.append({"position": n, "amount": amount, "year": year, "lender": lender})
        details = {
            "phones": _phones(r),
            "loans": loans,
            "equity": _money(r.get(cols["equity"])) if cols["equity"] else "",
            "balance": _money(r.get(cols["balance"])) if cols["balance"] else "",
        }
        out[P.address_key(addr, city.title())] = details
        out.setdefault(_loose_key(addr, city), details)
    return out


def _phones(r):
    """'413-555-0100 (Mobile); 413-555-0199 (DNC)' from PropStream's skip-trace columns
    (Phone 1, Phone 1 Type, Phone 1 DNC, ...)."""
    out = []
    for h, v in r.items():
        low = h.lower()
        if "phone" not in low or any(x in low for x in ("type", "dnc", "do not call", "status", "litigator")):
            continue
        digits = re.sub(r"\D", "", str(v or ""))
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) != 10:
            continue
        number = f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
        tags = []
        for k, flag in r.items():
            kl = k.lower()
            if kl.startswith(low) and kl != low:
                if "type" in kl and flag:
                    tags.append(str(flag).strip().title())
                if ("dnc" in kl or "do not call" in kl or "litigator" in kl) and str(flag).strip().lower() in (
                        "y", "yes", "true", "1", "dnc", "x"):
                    tags.append("DNC")
        out.append(number + (f" ({', '.join(tags)})" if tags else ""))
    return "; ".join(dict.fromkeys(out))


def apply_phones(calls, export_rows):
    """Fill the call list's Phone column from the export's skip-trace phones, unless a
    number was already typed in."""
    data = index(export_rows)
    for r in calls:
        if r.get("Phone"):
            continue
        d = (data.get(P.address_key(r.get("Property Address", ""), r.get("Town", "")))
             or data.get(_loose_key(r.get("Property Address", ""), r.get("Town", ""))))
        if d and d.get("phones"):
            r["Phone"] = d["phones"]
    return calls


def call_lookup_rows(calls, already):
    """Call-list owners (e.g. Land Court filings) not on the auction lookup, without a phone."""
    seen = {(x["Address"].upper(), x["City"].upper()) for x in already}
    out = []
    for r in calls:
        addr = re.split(r"\s+a/?k/?a\s+", r.get("Property Address", ""), flags=re.I)[0]
        if r.get("Phone") or not addr or addr.startswith("(") or (addr.upper(), r.get("Town", "").upper()) in seen:
            continue
        seen.add((addr.upper(), r.get("Town", "").upper()))
        out.append({"Address": addr, "City": r.get("Town", ""), "State": "MA", "Zip": "",
                    "Auction Date": r.get("Auction Date", ""), "Key": r["Key"]})
    return out


def _loose_key(addr, town):
    """House number + first street word + town: '14 Talcott St' matches '14 Talcott Ave'."""
    num, street, _ = P.split_address(addr)
    words = P.normalize_street(street).split()
    return f"{num}|{words[0] if words else ''}|{(town or '').upper()}"


def apply(rows, export_rows):
    """Fill mortgage columns on tracker rows from the PropStream export. Values already
    on the row (from the legal notice, or typed in) are kept."""
    data = index(export_rows)
    for r in rows:
        d = (data.get(P.address_key(r.get("Address", ""), r.get("Town", "")))
             or data.get(_loose_key(r.get("Address", ""), r.get("Town", ""))))
        if not d:
            continue
        r["Est. Equity"] = r.get("Est. Equity") or d["equity"]
        loans = d["loans"]
        if not loans:
            continue
        # The foreclosing loan is the one whose year matches the notice; else the first.
        loan = next((x for x in loans if r.get("Mortgage Year") and x["year"] == str(r["Mortgage Year"])), loans[0])
        r["Mortgage Amount"] = r.get("Mortgage Amount") or loan["amount"]
        r["Mortgage Year"] = r.get("Mortgage Year") or loan["year"]
        r["Lender / Mortgagee"] = r.get("Lender / Mortgagee") or loan["lender"]
        if loan["position"] == 2:
            r["1st or 2nd"] = "2nd"
        elif not r.get("1st or 2nd") or r["1st or 2nd"].endswith("(presumed)"):
            r["1st or 2nd"] = "1st (2nd exists)" if len(loans) > 1 else "1st"
    return rows


def lookup_rows(rows, today=None):
    """Upcoming auctions that still need mortgage data, in PropStream's import layout."""
    out = []
    for r in rows:
        addr = r.get("Address", "")
        if r.get("Mortgage Amount") or not addr or addr.startswith("("):
            continue
        if r.get("Status") not in ("Scheduled", "Postponed", "Notice only"):
            continue
        when = r.get("Auction Date", "")
        if today and when and datetime.strptime(when, "%m/%d/%Y").date() < today:
            continue  # already sold
        addr = re.split(r"\s+a/?k/?a\s+", addr, flags=re.I)[0]
        out.append({"Address": addr, "City": r.get("Town", ""), "State": "MA", "Zip": r.get("Zip", ""),
                    "Auction Date": r.get("Auction Date", ""), "Key": r["Key"]})
    return out

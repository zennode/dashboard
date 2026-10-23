"""Foreclosure legal notices from masspublicnotices.org (the MA newspapers' notice site).

The site is an ASP.NET form. We tick the counties, search for "mortgagee", and page
through the result list. Each result shows the first ~300 characters of the notice,
which normally names the borrower, the lender, the mortgage date and the registry
Book/Page, and often the premises address. The full-text pages sit behind a CAPTCHA,
so we only use the list snippets.
"""

import logging
import re
from datetime import date

from bs4 import BeautifulSoup

import parse as P

log = logging.getLogger(__name__)

HOME = "https://www.masspublicnotices.org/"
P_FORM = "ctl00$ContentPlaceHolder1$as1$"
P_GRID = "ctl00$ContentPlaceHolder1$WSExtendedGridNP1$GridView1$ctl01$"
COUNTY_INDEX = {"Berkshire": 1, "Franklin": 5, "Hampden": 6, "Hampshire": 7, "Worcester": 13}

REGISTRY_COUNTY = [
    (re.compile(r"Berkshire", re.I), "Berkshire"),
    (re.compile(r"Franklin", re.I), "Franklin"),
    (re.compile(r"Hampden", re.I), "Hampden"),
    (re.compile(r"Hampshire", re.I), "Hampshire"),
    (re.compile(r"Worcester", re.I), "Worcester"),
]
OTHER_REGISTRY = re.compile(
    r"(Barnstable|Bristol|Dukes|Essex|Middlesex|Nantucket|Norfolk|Plymouth|Suffolk)\s+(County|North|South|Northern|Southern)?.{0,25}Registry",
    re.I,
)

GIVEN_BY_RE = re.compile(
    r"(?:mortgage\s+(?:given|executed|granted)\s+by|executed\s+by)\s+(.+?)\s+(?:as\s+mortgagors?\s+)?(?:to|in\s+favor\s+of)\s+(.+?)"
    r"(?:,?\s+(?:dated|said\s+mortgage|and\s+recorded|recorded|as\s+mortgagee\b|its\s+successors)|\s*\.\.\.|$)",
    re.I | re.S,
)
NOMINEE_RE = re.compile(r"nominee\s+for\s+(.+?)(?:,|\s+its\s+successors|\s+dated|\s*\.\.\.|$)", re.I)
DATED_RE = re.compile(r"dated\s+(?:as\s+of\s+)?(" + P.MONTHS + r"\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})", re.I)
EXECUTED_ON_RE = re.compile(r"on\s+(\d{1,2}/\d{1,2}/\d{4})\s+a\s+certain\s+mortgage", re.I)
PRINCIPAL_RE = re.compile(r"(?:original\s+)?principal\s+(?:amount|sum)\s+of\s+\$\s?([\d,]+(?:\.\d{2})?)", re.I)
LAND_COURT_TO_RE = re.compile(r"ORDER OF NOTICE\s+TO:\s*(.+?)(?:\s+and\s+to\s+all\s+persons|$)", re.I | re.S)
LAND_COURT_PLAINTIFF_RE = re.compile(r"\(et seq\)?\)?:?\s*(.+?)\s+(?:claiming|has filed|,\s+claiming)", re.I | re.S)
DOCKET_RE = re.compile(r"\b(\d{2}\s*SM\s*\d{4,7})\b", re.I)
SECOND_RE = re.compile(r"second\s+mortgage|subordinate|junior\s+mortgage|subject\s+to\s+(?:a|the|any)\s+(?:first|prior|senior)\s+mortgage|home\s+equity|HELOC|line\s+of\s+credit", re.I)


def _form(soup):
    d = {}
    for i in soup.select("input"):
        name, typ = i.get("name"), (i.get("type") or "text").lower()
        if not name:
            continue
        if typ in ("hidden", "text"):
            d[name] = i.get("value", "")
        elif typ in ("checkbox", "radio") and i.has_attr("checked"):
            d[name] = i.get("value", "on")
    for sel in soup.select("select"):
        opt = sel.select_one("option[selected]") or sel.select_one("option")
        if opt is not None and sel.get("name"):
            d[sel["name"]] = opt.get("value")
    return d


def _drop_buttons(d):
    for k in [k for k in d if k.startswith(P_FORM + "btn")]:
        d.pop(k)
    return d


def search(http, counties, days, keyword="mortgagee", max_pages=40):
    """Yield raw result dicts: id, newspaper, published, snippet."""
    r = http.get(HOME)
    base = r.url.rsplit("/", 1)[0]  # session id lives in the path: /(S(xxxx))/
    soup = BeautifulSoup(r.text, "lxml")
    # Each county checkbox triggers a postback; tick them one at a time.
    for county in counties:
        idx = COUNTY_INDEX[county]
        d = _form(soup)
        d[f"{P_FORM}lstCounty${idx}"] = "on"
        d["__EVENTTARGET"], d["__EVENTARGUMENT"] = f"{P_FORM}lstCounty${idx}", ""
        soup = BeautifulSoup(http.post(base + "/default.aspx", data=_drop_buttons(d)).text, "lxml")

    d = _form(soup)
    d.update({P_FORM + "txtSearch": keyword, P_FORM + "rdoType": "AND",
              P_FORM + "dateRange": "rbLastNumDays", P_FORM + "txtLastNumDays": str(days),
              P_FORM + "btnGo": ""})
    r = http.post(base + "/default.aspx", data=d)
    url = r.url
    soup = BeautifulSoup(r.text, "lxml")

    d = _drop_buttons(_form(soup))
    d[P_GRID + "ddlPerPage"] = "50"
    d["__EVENTTARGET"], d["__EVENTARGUMENT"] = P_GRID + "ddlPerPage", ""
    soup = BeautifulSoup(http.post(url, data=d).text, "lxml")

    for page in range(max_pages):
        for row in _rows(soup):
            yield row
        m = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", soup.get_text(" "))
        if not m or int(m.group(1)) >= int(m.group(2)):
            break
        d = _drop_buttons(_form(soup))
        d[P_GRID + "btnNext.x"], d[P_GRID + "btnNext.y"] = "5", "5"
        soup = BeautifulSoup(http.post(url, data=d).text, "lxml")


def _rows(soup):
    for tb in soup.select("table.nested"):
        pk = tb.select_one("input[name$=hdnPKValue]")
        left = tb.select_one("div.left")
        cells = tb.find_all("td")
        if not pk or not left or len(cells) < 3:
            continue
        lines = [x.strip() for x in left.get_text("\n").split("\n") if x.strip()]
        snippet = P.clean(cells[-1].get_text(" ")).replace("click 'view' to open the full text.", "").strip(" .")
        right = P.clean(tb.select_one("div.right").get_text(" ")) if tb.select_one("div.right") else ""
        yield {
            "notice_id": pk.get("value"),
            "newspaper": lines[0] if lines else "",
            "published": lines[1] if len(lines) > 1 else "",
            "paper_location": right,
            "snippet": snippet,
        }


COURT_RE = re.compile(r"\b(Berkshire|Franklin|Hampden|Hampshire|Worcester)\s+(?:County\s+)?(?:Superior|Housing|District|Probate)", re.I)


def _county_from_text(text):
    court = COURT_RE.search(text)
    if court:
        return court.group(1).title()
    reg = re.search(r"([A-Z][\w ()]+?)\s+Registry", text)
    if reg:
        for rx, county in REGISTRY_COUNTY:
            if rx.search(reg.group(1)):
                return county
        if OTHER_REGISTRY.search(text):
            return None
    return ""


def parse_notice(row, today=None):
    """Pull structured fields from a result snippet. Returns None for notices that are
    clearly outside our counties."""
    today = today or date.today()
    text = P.unhyphen(row["snippet"])
    kind = "Land Court filing" if re.search(r"LAND COURT|ORDER OF NOTICE|Servicemembers", text, re.I) else "Mortgagee's sale"
    if kind == "Mortgagee's sale" and not re.search(r"sale|foreclos", text, re.I):
        return None

    addrs = P.find_addresses(text)
    address, town, county = (addrs[0][2], addrs[0][3], addrs[0][4]) if addrs else ("", "", "")
    reg_county = _county_from_text(text)
    if reg_county is None:
        return None  # recorded at a registry outside our counties
    county = county or reg_county
    if not county:
        # Fall back to the newspaper's own county ("City: Springfield County: Hampden").
        m = re.search(r"County:\s*(\w+)", row.get("paper_location", ""))
        if m and m.group(1).title() in COUNTY_INDEX:
            county = m.group(1).title()
            row["county_from_paper"] = True
    if county not in COUNTY_INDEX:
        return None

    borrower = lender = ""
    g = GIVEN_BY_RE.search(text)
    if g:
        borrower = P.clean(g.group(1)).strip(" ,")
        lender = P.clean(g.group(2)).strip(" ,")
        nom = NOMINEE_RE.search(text[g.start(2):])
        if nom:
            lender = P.clean(nom.group(1)).strip(" ,")
        if text.rstrip(" .").endswith(lender):  # the snippet was cut off mid-name
            lender = lender + "..." if len(lender) >= 6 else ""
    if kind == "Land Court filing":
        to = LAND_COURT_TO_RE.search(text)
        borrower = P.clean(to.group(1)).strip(" ,;") if to else borrower
        pl = LAND_COURT_PLAINTIFF_RE.search(text)
        lender = P.clean(pl.group(1)).strip(" ,") if pl else lender

    mdate = ""
    dm = DATED_RE.search(text) or EXECUTED_ON_RE.search(text)
    if dm:
        d = P.parse_date(dm.group(1), today)
        mdate = d.isoformat() if d else ""
    pub = P.parse_date(row["published"], today)
    principal = PRINCIPAL_RE.search(text)
    docket = DOCKET_RE.search(text)
    return {
        "notice_id": row["notice_id"],
        "kind": kind,
        "newspaper": row["newspaper"],
        "published": row["published"],
        "first_published": pub.isoformat() if pub else "",
        "last_published": pub.isoformat() if pub else "",
        "address": address,
        "town": town,
        "county": county,
        "borrower": borrower,
        "lender": lender,
        "mortgage_date": mdate,
        "mortgage_amount": P.fmt_money(float(principal.group(1).replace(",", ""))) if principal else "",
        "book_page": P.parse_book_page(text),
        "lien_position": "2nd" if SECOND_RE.search(text) else "",
        "docket": P.clean(docket.group(1)).upper() if docket else "",
        "snippet": text,
        "notice_url": f"{HOME}Details.aspx?ID={row['notice_id']}",
    }


def collect(http, counties, days, today=None):
    """All parsed notices in our counties, one per foreclosure (repeat publications merged)."""
    out = {}
    for row in search(http, counties, days):
        n = parse_notice(row, today)
        if not n:
            continue
        key = (n["book_page"] or n["docket"] or f"{n['borrower']}|{n['mortgage_date']}").upper()
        if not key.strip("|"):
            key = n["notice_id"]
        prev = out.get(key)
        if prev:
            for k, v in n.items():
                if v and not prev.get(k):
                    prev[k] = v
            if n["first_published"]:
                prev["first_published"] = min(filter(None, [prev["first_published"], n["first_published"]]))
                prev["last_published"] = max(prev["last_published"], n["last_published"])
            prev["publications"] += 1
        else:
            n["publications"] = 1
            out[key] = n
    return list(out.values())

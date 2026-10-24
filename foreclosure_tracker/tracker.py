"""Foreclosure auction tracker for western/central Massachusetts.

Each run:
  1. scrapes every auctioneer site in config.yaml for auctions in our towns,
  2. pulls foreclosure legal notices (mortgagee's sales + Land Court filings),
  3. matches notices to listings (registry Book/Page, address, or owner name),
  4. adds assessor data from MassGIS (owner, year built, living area, lot, ...),
  5. compares with the previous run to catch new, postponed and cancelled auctions,
  6. rewrites the tracker tabs in Google Sheets (keeping anything typed in by hand).

Usage:
  python tracker.py                 # update Google Sheets
  python tracker.py --csv out/      # write CSVs instead (no Google credentials needed)
"""

import argparse
import concurrent.futures as cf
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import yaml

import assessor_index
import parse as P
import propstream
from http_client import Http
from massgis import MassGIS, owner_patterns
from sheets import CsvWriter, SheetWriter
from sources import public_notices
from sources.auction_sites import scrape_site
from towns import COUNTY_TOWNS, TOWN_IDS, TOWN_TO_COUNTY

log = logging.getLogger("tracker")
HERE = os.path.dirname(os.path.abspath(__file__))
EASTERN = ZoneInfo("America/New_York")

REGISTRY_URLS = {
    "Hampden": "https://www.masslandrecords.com/Hampden/",
    "Hampshire": "https://www.masslandrecords.com/Hampshire/",
    "Franklin": "https://www.masslandrecords.com/Franklin/",
    "Worcester": "https://www.masslandrecords.com/Worcester/",
    "Berkshire": "https://www.masslandrecords.com/",
}
WORCESTER_NORTH = {"Ashburnham", "Fitchburg", "Gardner", "Lancaster", "Leominster", "Lunenburg",
                   "Sterling", "Westminster", "Winchendon"}

TRACKER_COLS = [
    "Mortgage Amount", "Mortgage Year", "Lender / Mortgagee", "1st or 2nd", "Auction Date", "Time",
    "Address", "Town", "County", "Tax / Water / Sewer Liens", "Specs", "Year Built", "Lot Sq Ft",
    "Acres", "Gross Living Area", "Total Building Area", "Septic / Sewer", "Deposit",
    "Auction Company", "Status", "Original Date", "Times Postponed", "Status Notes",
    "Owner (Assessor)", "Borrower (Legal Notice)", "Owner Mailing Address", "Owner Occupied",
    "Parcel ID", "Assessed Value", "Est. Equity", "Last Sale", "Registry Book/Page", "Registry Search",
    "Listing Link", "Legal Notice", "Found On", "First Seen", "Last Checked", "Team Notes", "Key",
]
# Columns people fill in by hand: a value typed in the sheet wins over an empty scraped one,
# and Team Notes is always kept as typed.
TRACKER_KEEP_IF_EMPTY = {"Mortgage Amount", "Mortgage Year", "Lender / Mortgagee", "1st or 2nd",
                         "Tax / Water / Sewer Liens", "Septic / Sewer", "Specs", "Est. Equity"}
TRACKER_ALWAYS_KEEP = {"Team Notes"}

CALL_COLS = [
    "Priority", "Stage", "Auction Date", "Days Left", "Status", "Times Postponed", "Owner (Assessor)",
    "Borrower (Legal Notice)", "Property Address", "Town", "Owner Mailing Address", "Owner Occupied",
    "Lender / Mortgagee", "Mortgage Year", "Assessed Value", "Last Sale", "Auction Company",
    "Legal Notice", "Why Call", "Phone", "Called?", "Call Date", "Outcome", "Call Notes", "Key",
]
CALL_ALWAYS_KEEP = {"Phone", "Called?", "Call Date", "Outcome", "Call Notes"}

CHANGE_COLS = ["Detected (ET)", "Change", "Address", "Town", "Auction Date", "Previous", "Now",
               "Auction Company", "Found On", "Key"]
SOURCE_COLS = ["Source", "Kind", "URL", "Last Run (ET)", "Result", "Listings In Our Towns", "Note"]


# ---------------------------------------------------------------- config & state

def load_config(path):
    with open(path) as f:
        cfg = yaml.safe_load(f)
    counties = cfg.get("counties") or list(COUNTY_TOWNS)
    towns = cfg.get("towns") or [t for c in counties for t in COUNTY_TOWNS[c]]
    unknown = [t for t in towns if t not in TOWN_TO_COUNTY]
    if unknown:
        raise SystemExit(f"Unknown towns in config.yaml: {unknown}")
    cfg["counties"], cfg["town_set"] = counties, set(towns)
    cfg["likely_septic"] = set(cfg.get("likely_septic_towns") or [])
    return cfg


def load_state(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True, default=str)
    os.replace(tmp, path)


# ---------------------------------------------------------------- collection

def scrape_sites(cfg, today, now_str):
    listings, health = [], []

    def one(site):
        try:
            recs = scrape_site(Http(), site, today, cfg["town_set"])
            return site, recs, None
        except Exception as e:
            return site, [], f"{type(e).__name__}: {str(e)[:150]}"

    with cf.ThreadPoolExecutor(8) as ex:
        for site, recs, err in ex.map(one, cfg["auction_sites"]):
            recs = [r for r in recs if r["town"] in cfg["town_set"]]
            listings += recs
            note = site.get("note", "")
            if not err and not recs:
                note = note or "Nothing in our towns right now (or the site loads its list with JavaScript - spot-check it)."
            health.append({"Source": site["name"], "Kind": "Auction site", "URL": site["url"],
                           "Last Run (ET)": now_str, "Result": "ERROR" if err else "OK",
                           "Listings In Our Towns": len(recs), "Note": err or note})
            log.info("%-22s %3d listings %s", site["name"], len(recs), err or "")
    return listings, health


def collect_notices(cfg, today, now_str):
    try:
        notices = public_notices.collect(Http(), cfg["counties"], cfg.get("public_notice_lookback_days", 45), today)
        notices = [n for n in notices if not n["town"] or n["town"] in cfg["town_set"]]
        status = {"Result": "OK", "Listings In Our Towns": len(notices),
                  "Note": "Mortgagee's sale notices and Land Court filings, from the result snippets"}
    except Exception as e:
        log.exception("public notices failed")
        notices = []
        status = {"Result": "ERROR", "Listings In Our Towns": 0, "Note": f"{type(e).__name__}: {str(e)[:150]}"}
    health = {"Source": "masspublicnotices.org", "Kind": "Legal notices", "URL": public_notices.HOME,
              "Last Run (ET)": now_str, **status}
    return notices, health


# ---------------------------------------------------------------- merging

def merge_listings(listings, today):
    """One record per property. When sources disagree, the latest auction date wins (a
    postponement pushes the date out), and a cancellation for that date wins over 'scheduled'."""
    by_key = {}
    for r in listings:
        if r["status"] == "Sold" and not r["auction_date"]:
            continue  # an old "sold" result left on the page
        if r["auction_date"] and r["auction_date"] < (today - timedelta(days=60)).isoformat():
            continue  # a stale page still showing last year's sale
        key = P.address_key(r["address"], r["town"])
        by_key.setdefault(key, []).append(r)
    merged = {}
    for key, recs in by_key.items():
        recs.sort(key=lambda r: (r["auction_date"] or "", r["status"] == "Cancelled"), reverse=True)
        best = dict(recs[0])
        for r in recs[1:]:
            for k, v in r.items():
                if v and not best.get(k):
                    best[k] = v
            if r["auction_date"] and r["auction_date"] < (best["auction_date"] or "9") and not best.get("original_date"):
                best["original_date"] = r["auction_date"]
        best["sources"] = sorted({r["source"] for r in recs})
        best["links"] = sorted({r["detail_url"] or r["source_url"] for r in recs})
        merged[key] = best
    return merged


def _bp_digits(bp):
    return "-".join(re.findall(r"\d+", bp or ""))


def match_notices(auctions, notices):
    """Attach each notice to the auction it belongs to. Returns notices that matched nothing."""
    by_bp = {_bp_digits(a.get("book_page")): k for k, a in auctions.items() if a.get("book_page")}
    by_addr = dict((k, k) for k in auctions)
    unmatched = []
    for n in notices:
        key = None
        if n["book_page"] and _bp_digits(n["book_page"]) in by_bp:
            key = by_bp[_bp_digits(n["book_page"])]
        elif n["address"] and n["town"]:
            k = P.address_key(n["address"], n["town"])
            key = by_addr.get(k)
        if not key and n["borrower"]:
            pats = owner_patterns(n["borrower"])
            for k, a in auctions.items():
                owner = ((a.get("gis") or {}).get("owner") or "").upper()
                if owner and a["county"] == n["county"] and any(owner.startswith(p) for p in pats):
                    key = k
                    break
        if not key and n.get("auction_date"):
            key = _match_by_date_and_town(auctions, n)
        if key:
            auctions[key].setdefault("notices", []).append(n)
        else:
            unmatched.append(n)
    return unmatched


def _match_by_date_and_town(auctions, n):
    """A listing with the same sale date (or original date) in a town the notice names.
    Only used when exactly one listing fits."""
    towns = {n["town"]} if n.get("town") else set(n.get("probe_towns", []))
    dates = {n["auction_date"], n.get("original_date")} - {None, ""}
    hits = [k for k, a in auctions.items()
            if a.get("town") in towns and {a.get("auction_date"), a.get("original_date")} & dates
            and not a.get("notices")]
    return hits[0] if len(hits) == 1 else None


def notice_only_records(notices, today):
    """Auctions we only know about from a legal notice (not on any auctioneer site)."""
    out = {}
    for n in notices:
        if n["kind"] != "Mortgagee's sale":
            continue
        last = n.get("last_published") and date.fromisoformat(n["last_published"])
        if last and (today - last).days > 28 and not n.get("auction_date"):
            continue  # last ran a month ago: the sale has almost certainly happened
        key = ("N-" + P.address_key(n["address"], n["town"])) if n["address"] else "N-" + _notice_key(n)
        first = n.get("first_published") and date.fromisoformat(n["first_published"])
        if n.get("auction_date"):
            note = "Only in the legal notice - not on any auctioneer website"
        elif first:
            note = (f"Sale date not found in the notice text. First published {first:%m/%d}, so the sale "
                    f"is likely {first + timedelta(days=21):%m/%d}-{first + timedelta(days=35):%m/%d}")
        else:
            note = ""
        out[key] = {
            "address": n["address"], "town": n["town"], "county": n["county"],
            "auction_date": n.get("auction_date", ""), "auction_time": n.get("auction_time", ""),
            "status": "Notice only",
            "original_date": n.get("original_date", ""), "deposit": n.get("deposit", ""),
            "auction_company": n.get("auction_company", ""), "book_page": n["book_page"], "specs": "",
            "sewer": "", "listing_text": "", "sources": ["Legal notice"], "links": [], "notices": [n],
            "status_note": note,
        }
    return out


def _town_from_notice(named, county):
    """The town a notice names, when the address itself wasn't in its excerpt. Every
    Worcester County notice mentions "Worcester" (the registry), so the city of Worcester
    only counts when no other town is named; the same goes for the town of Hampden."""
    others = [t for t in named if t != county]
    if len(others) == 1:
        return others[0]
    if not others and county in named:
        return county
    return ""


def _notice_key(n):
    import hashlib
    raw = (n["book_page"] or n["docket"] or f"{n['borrower']}|{n['mortgage_date']}|{n['county']}").upper()
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- enrichment

def enrich(auctions, gis, towns):
    for a in auctions.values():
        if a.get("address") and a.get("town"):
            for alt in re.split(r"\s+a/?k/?a\s+|\s+also known as\s+", a["address"], flags=re.I):
                a["gis"] = gis.by_address(alt, a["town"]) or {}
                if a["gis"]:
                    break
        elif a.get("notices") and a["notices"][0].get("borrower"):
            # Notice with a borrower but no address: find the property by owner name.
            n = a["notices"][0]
            county_towns = [t for t in COUNTY_TOWNS[n["county"]] if t in towns]
            named = [t for t in n.get("probe_towns", []) if t in county_towns]
            res = (gis.by_owner(n["borrower"], named) if named else None) or gis.by_owner(n["borrower"], county_towns)
            if res:
                a["gis"] = res
                a["address"] = res["site_address"]
                a["town"] = res["town"]
            elif not a.get("town"):
                a["town"] = _town_from_notice(named, n["county"])


# ---------------------------------------------------------------- rows

def build_row(key, a, cfg):
    g = a.get("gis") or {}
    notices = a.get("notices") or []
    n = notices[0] if notices else {}
    text = a.get("listing_text", "") + " " + " ".join(x["snippet"] for x in notices)

    lien = n.get("lien_position") or ("2nd" if public_notices.SECOND_RE.search(text) else "")
    if not lien and n:
        lien = "1st (presumed)"
    sewer = a.get("sewer") or ("Likely SEPTIC - verify" if a["town"] in cfg["likely_septic"] else "Verify w/ BOH")
    specs = a.get("specs") or ""
    gis_specs = ", ".join(str(x) for x in [g.get("use"), g.get("style"),
                                           f"{g['rooms']} rooms" if g.get("rooms") else ""] if x)
    if gis_specs:
        specs = f"{specs} ({gis_specs})" if specs else gis_specs
    county = a.get("county") or TOWN_TO_COUNTY.get(a["town"], "")
    registry = REGISTRY_URLS.get(county, "")
    if county == "Worcester" and a["town"] in WORCESTER_NORTH:
        registry = "https://www.masslandrecords.com/"
    times_postponed = a.get("times_postponed", 0)
    mortgage_year = (n.get("mortgage_date") or "")[:4]
    last_sale = ""
    if g.get("last_sale_date"):
        last_sale = f"{g['last_sale_date']} for {P.fmt_money(g.get('last_sale_price'))}" if g.get("last_sale_price") else g["last_sale_date"]
    status_note = a.get("status_note", "")
    if a["status"] == "Postponed" and a.get("original_date"):
        status_note = status_note or f"Postponed from {_us(a['original_date'])}"
    return {
        "Key": key,
        "Mortgage Amount": n.get("mortgage_amount", ""),
        "Mortgage Year": mortgage_year,
        "Lender / Mortgagee": _lender(notices),
        "1st or 2nd": lien,
        "Auction Date": _us(a["auction_date"]),
        "Time": a.get("auction_time") or n.get("auction_time", ""),
        "Address": a.get("address", "") or (f"(borrower: {n['borrower']})" if n.get("borrower") else
                                            "(see legal notice)" if n else ""),
        "Town": a.get("town", ""),
        "County": county,
        "Tax / Water / Sewer Liens": "",
        "Specs": specs,
        "Year Built": g.get("year_built", ""),
        "Lot Sq Ft": g.get("lot_sqft", ""),
        "Acres": g.get("lot_acres", ""),
        "Gross Living Area": g.get("living_area", ""),
        "Total Building Area": g.get("building_area", ""),
        "Septic / Sewer": sewer,
        "Deposit": a.get("deposit") or n.get("deposit", ""),
        "Auction Company": a.get("auction_company", "") or n.get("auction_company", "") or ", ".join(s for s in a.get("sources", []) if s != "Legal notice"),
        "Status": a["status"],
        "Original Date": _us(a.get("original_date", "")),
        "Times Postponed": times_postponed or "",
        "Status Notes": status_note,
        "Owner (Assessor)": g.get("owner", ""),
        "Borrower (Legal Notice)": n.get("borrower", ""),
        "Owner Mailing Address": g.get("owner_mailing", ""),
        "Owner Occupied": g.get("owner_occupied", ""),
        "Parcel ID": g.get("parcel_id", ""),
        "Assessed Value": P.fmt_money(g.get("assessed_value")) if g.get("assessed_value") else "",
        "Last Sale": last_sale,
        "Registry Book/Page": a.get("book_page") or n.get("book_page", ""),
        "Registry Search": registry,
        "Listing Link": (a.get("links") or [""])[0],
        "Legal Notice": " ".join(x["notice_url"] for x in notices[:2]),
        "Found On": ", ".join(a.get("sources", [])),
        "First Seen": a.get("first_seen", ""),
        "Last Checked": a.get("last_seen", ""),
        "Team Notes": "",
    }


def _lender(notices):
    for n in notices:
        lender = n.get("lender", "")
        if lender and not lender.upper().startswith("MORTGAGE ELECTR"):
            return lender
    return "MERS nominee - see notice" if notices and notices[0].get("lender") else ""


def _us(iso):
    if not iso:
        return ""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%m/%d/%Y")
    except ValueError:
        return iso


def _minutes(t):
    m = re.match(r"(\d+):(\d+) ([AP])M", t or "")
    if not m:
        return 9999
    return (int(m.group(1)) % 12 + (12 if m.group(3) == "P" else 0)) * 60 + int(m.group(2))


def keep_sheet_edits(rows, existing, always, if_empty):
    old = {r.get("Key"): r for r in existing if r.get("Key")}
    for r in rows:
        o = old.get(r["Key"])
        if not o:
            continue
        for col in always:
            r[col] = o.get(col, "")
        for col in if_empty:
            if not r.get(col) and o.get(col):
                r[col] = o[col]
    return rows


# ---------------------------------------------------------------- change detection

def detect_changes(current, state, sources_ok, today, now_str):
    """Compare this run with the last one. Updates state['auctions'] in place."""
    prev_all = state.setdefault("auctions", {})
    events = []

    def event(kind, key, a, before="", after=""):
        events.append({"Detected (ET)": now_str, "Change": kind, "Address": a.get("address", ""),
                       "Town": a.get("town", ""), "Auction Date": _us(a.get("auction_date", "")),
                       "Previous": before, "Now": after, "Auction Company": a.get("auction_company", ""),
                       "Found On": ", ".join(a.get("sources", [])), "Key": key})

    first_run = not prev_all
    for key, a in current.items():
        p = prev_all.get(key)
        if not p:
            if not first_run:
                event("New legal notice (not on auction sites)" if a["status"] == "Notice only" else "New auction",
                      key, a, "", f"{_us(a['auction_date'])} {a.get('auction_time', '')} {a['status']}".strip())
            a["times_postponed"] = 1 if a["status"] == "Postponed" else 0
            a["first_seen"] = now_str
        else:
            a["first_seen"] = p.get("first_seen", now_str)
            a["times_postponed"] = p.get("times_postponed", 0)
            if p.get("status") != a["status"]:
                event(f"Status: {p.get('status')} -> {a['status']}", key, a, p.get("status", ""), a["status"])
            if p.get("auction_date") and a["auction_date"] and p["auction_date"] != a["auction_date"]:
                kind = "Postponed" if a["auction_date"] > p["auction_date"] else "Date moved earlier"
                if kind == "Postponed":
                    a["times_postponed"] += 1
                event(kind, key, a, _us(p["auction_date"]), _us(a["auction_date"]))
            elif p.get("auction_time") and a.get("auction_time") and p["auction_time"] != a["auction_time"]:
                event("Time changed", key, a, p["auction_time"], a["auction_time"])
        a["missing_runs"] = 0
        a["last_seen"] = now_str

    # Listings that disappeared from the sites they were on (only if those sites loaded fine).
    for key, p in prev_all.items():
        if key in current or key.startswith("N-"):
            continue
        future = p.get("auction_date") and p["auction_date"] >= today.isoformat()
        if not future or p.get("status") in ("Cancelled", "Removed from site"):
            continue
        if not all(sources_ok.get(s, False) for s in p.get("sources", [])):
            continue
        p["missing_runs"] = p.get("missing_runs", 0) + 1
        if p["missing_runs"] == 2:
            p["status"] = "Removed from site"
            p["status_note"] = "No longer listed - often means cancelled; call the auctioneer to confirm"
            event("Removed from auction site", key, p, "listed", "not listed")

    merged = {**prev_all, **current}
    # Forget auctions more than 120 days old so the state file stays small, and any dated
    # more than four months out: foreclosure sales aren't set that far ahead, so such a date
    # is a misread (an old listing with no year).
    cutoff = (today - timedelta(days=120)).isoformat()
    too_far = (today + timedelta(days=125)).isoformat()
    state["auctions"] = {k: v for k, v in merged.items()
                         if cutoff <= (v.get("auction_date") or v.get("last_seen", "")[:10] or cutoff) <= too_far}
    return events


# ---------------------------------------------------------------- call list

def call_rows(rows, auctions, land_court, cfg, today):
    out = []
    for r in rows:
        a = auctions.get(r["Key"], {})
        if r["Status"] in ("Cancelled", "Sold", "Removed from site"):
            continue
        d = a.get("auction_date")
        days_left = (date.fromisoformat(d) - today).days if d else ""
        if d and days_left < 0:
            continue
        owner_occ = r["Owner Occupied"]
        postponed = int(r["Times Postponed"] or 0)
        reasons = []
        if owner_occ == "Yes":
            reasons.append("owner lives there")
        if postponed:
            reasons.append(f"postponed {postponed}x - lender may be open to a workout or short sale")
        if days_left != "" and days_left <= 14:
            reasons.append("auction within 2 weeks")
        if r["Status"] == "Notice only":
            reasons.append("legal notice only - not on auction sites yet")
        if owner_occ == "Yes" and (postponed or (days_left != "" and days_left <= 30)):
            priority = "1 - High"
        elif owner_occ.startswith("No"):
            priority = "3 - Investor/absentee owner"
        else:
            priority = "2 - Medium"
        out.append({
            "Key": r["Key"], "Priority": priority, "Stage": "Auction scheduled" if d else "Mortgagee's sale notice",
            "Auction Date": r["Auction Date"], "Days Left": days_left, "Status": r["Status"],
            "Times Postponed": r["Times Postponed"], "Owner (Assessor)": r["Owner (Assessor)"],
            "Borrower (Legal Notice)": r["Borrower (Legal Notice)"], "Property Address": r["Address"],
            "Town": r["Town"], "Owner Mailing Address": r["Owner Mailing Address"],
            "Owner Occupied": owner_occ, "Lender / Mortgagee": r["Lender / Mortgagee"],
            "Mortgage Year": r["Mortgage Year"], "Assessed Value": r["Assessed Value"],
            "Last Sale": r["Last Sale"], "Auction Company": r["Auction Company"],
            "Legal Notice": r["Legal Notice"], "Why Call": "; ".join(reasons),
        })
    # Land Court (Servicemembers) filings: the earliest public sign of a foreclosure.
    for n in land_court:
        g = n.get("gis") or {}
        out.append({
            "Key": "LC-" + (n["docket"] or _notice_key(n)), "Priority": "2 - Medium",
            "Stage": "Pre-foreclosure (Land Court filing)", "Auction Date": "", "Days Left": "",
            "Status": f"Docket {n['docket']}" if n["docket"] else "Land Court filing",
            "Times Postponed": "", "Owner (Assessor)": g.get("owner", ""),
            "Borrower (Legal Notice)": n["borrower"], "Property Address": g.get("site_address", n["address"]),
            "Town": n["town"],
            "Owner Mailing Address": g.get("owner_mailing", ""), "Owner Occupied": g.get("owner_occupied", ""),
            "Lender / Mortgagee": n["lender"], "Mortgage Year": (n["mortgage_date"] or "")[:4],
            "Assessed Value": P.fmt_money(g.get("assessed_value")) if g.get("assessed_value") else "",
            "Last Sale": g.get("last_sale_date", ""), "Auction Company": "",
            "Legal Notice": n["notice_url"],
            "Why Call": f"Lender filed in Land Court ({n['county']} County paper) - months before any auction",
        })
    out.sort(key=lambda r: (r["Priority"], r["Days Left"] if r["Days Left"] != "" else 999))
    return out


# ---------------------------------------------------------------- main

def notice_facts(cfg, state, today):
    """Sale dates, deposits, auctioneers and towns found by searching the notices' full
    text. That takes a few hundred searches, so it runs once a day and only looks at
    notices published since the last time; results are kept in the state file."""
    store = state.setdefault("notice_facts", {})
    lookback = cfg.get("public_notice_lookback_days", 90)
    # Bump when probe() starts looking for something new, so old notices get searched again.
    version = 2
    if state.get("notice_facts_version") != version:
        state["notice_facts_date"], state["notice_facts_version"] = None, version
    last = state.get("notice_facts_date")
    if last != today.isoformat():
        gap = (today - date.fromisoformat(last)).days if last else 9999
        days = min(lookback, gap + 3)
        try:
            found = public_notices.probe(lambda: Http(), cfg["counties"], days, today, sorted(cfg["town_set"]),
                                         past_days=days)
            for nid, fields in found.items():
                slot = store.setdefault(nid, {})
                for field, values in fields.items():
                    slot[field] = sorted(set(slot.get(field, [])) | set(values))
            state["notice_facts_date"] = today.isoformat()
        except Exception:
            log.exception("notice full-text lookups failed; will retry next run")
        # Forget notices older than ~3 months.
        keep = {int(k) for k in store if k.isdigit()}
        if len(keep) > 3000:
            cutoff = sorted(keep)[-3000]
            store = state["notice_facts"] = {k: v for k, v in store.items() if not k.isdigit() or int(k) >= cutoff}
    return {nid: {f: set(v) for f, v in fields.items()} for nid, fields in store.items()}


INACTIVE = ("Cancelled", "Postponed", "Sold", "Removed from site")


def run(cfg, writer, state_path, today=None):
    now = datetime.now(EASTERN)
    today = today or now.date()
    now_str = now.strftime("%Y-%m-%d %H:%M")
    state = load_state(state_path)
    assessor_dir = os.path.join(os.path.dirname(state_path), "assessor")
    assessor_index.ensure(Http(timeout=120), assessor_dir, {TOWN_IDS[t] for t in cfg["town_set"]})
    gis = MassGIS(assessor_dir)

    listings, health = scrape_sites(cfg, today, now_str)
    notices, notice_health = collect_notices(cfg, today, now_str)
    health.append(notice_health)

    auctions = merge_listings(listings, today)
    enrich(auctions, gis, cfg["town_set"])  # assessor data first, so owner-name matching works
    sale_notices = [n for n in notices if n["kind"] == "Mortgagee's sale"]
    land_court = [n for n in notices if n["kind"] == "Land Court filing"]
    if notice_health["Result"] == "OK":
        public_notices.apply_probe(sale_notices, notice_facts(cfg, state, today))
    unmatched = match_notices(auctions, sale_notices)
    notice_only = notice_only_records(unmatched, today)
    enrich(notice_only, gis, cfg["town_set"])
    # Drop notice-only records whose property turned out to be outside our towns.
    notice_only = {k: a for k, a in notice_only.items() if not a["town"] or a["town"] in cfg["town_set"]}
    auctions.update(notice_only)

    for n in land_court:
        if n["address"]:
            n["gis"] = gis.by_address(n["address"], n["town"]) or {}
        else:
            n["gis"] = gis.by_owner(n["borrower"], [t for t in COUNTY_TOWNS[n["county"]] if t in cfg["town_set"]]) or {}
            n["town"] = n["gis"].get("town", "")
    # Keep filings we could place in one of our towns.
    land_court = [n for n in land_court if n["town"] in cfg["town_set"]]

    sources_ok = {h["Source"]: h["Result"] == "OK" for h in health}
    sources_ok["Legal notice"] = notice_health["Result"] == "OK"
    events = detect_changes(auctions, state, sources_ok, today, now_str)
    all_auctions = state["auctions"]

    keep_from = (today - timedelta(days=cfg.get("keep_days_past", 14))).isoformat()
    rows = []
    for key, a in all_auctions.items():
        if a.get("auction_date") and a["auction_date"] < keep_from:
            continue
        if key.startswith("N-") and key not in auctions:
            continue  # notice-only record that has since matched a listing or aged out
        rows.append(build_row(key, a, cfg))
    rows.sort(key=lambda r: (r["Auction Date"][6:] + r["Auction Date"][:5] if r["Auction Date"] else "9999",
                             _minutes(r["Time"]), r["Town"]))

    tabs = cfg["tabs"]
    # Rows move between the two tabs as their status changes, so look for typed-in notes on both.
    typed = writer.read_rows(tabs["tracker"]) + writer.read_rows(tabs["cancelled"])
    rows = keep_sheet_edits(rows, typed, TRACKER_ALWAYS_KEEP, TRACKER_KEEP_IF_EMPTY)
    rows = propstream.apply(rows, writer.read_rows(tabs["propstream_export"]))
    writer.write_table(tabs["propstream_lookup"], propstream.LOOKUP_COLS, propstream.lookup_rows(rows, today))
    writer.write_table(tabs["tracker"], TRACKER_COLS, [r for r in rows if r["Status"] not in INACTIVE],
                       status_col="Status")
    writer.write_table(tabs["cancelled"], TRACKER_COLS, [r for r in rows if r["Status"] in INACTIVE],
                       status_col="Status")
    calls = call_rows(rows, all_auctions, land_court, cfg, today)
    calls = keep_sheet_edits(calls, writer.read_rows(tabs["call_list"]), CALL_ALWAYS_KEEP, set())
    writer.write_table(tabs["call_list"], CALL_COLS, calls, status_col="Status")
    writer.append_rows(tabs["changes"], CHANGE_COLS, events)
    writer.write_table(tabs["sources"], SOURCE_COLS, health)

    save_state(state_path, state)
    log.info("Done: %d auctions on tracker, %d to call, %d changes", len(rows), len(calls), len(events))
    return rows, calls, events, health


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    ap.add_argument("--state", default=os.path.join(HERE, "state", "state.json"))
    ap.add_argument("--csv", metavar="FOLDER", help="write CSV files here instead of Google Sheets")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    os.makedirs(os.path.dirname(args.state), exist_ok=True)
    writer = CsvWriter(args.csv) if args.csv else SheetWriter(cfg["spreadsheet_id"])
    run(cfg, writer, args.state)


if __name__ == "__main__":
    main()

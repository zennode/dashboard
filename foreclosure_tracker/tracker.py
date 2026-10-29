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
import calendar_sync
import parse as P
import propstream
from http_client import Http
from massgis import MassGIS, owner_matches, owner_patterns
from massgis import last_names as massgis_last_names
from sheets import CsvWriter, SheetWriter
from sources import public_notices, xome
from sources.auction_sites import MUNICIPAL_PREFIX, scrape_site
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
    "Opening Bid", "Auction Company", "Law Firm (Notice)", "Status", "Original Date", "Times Postponed", "Status Notes",
    "Owner (Assessor)", "Borrower (Legal Notice)", "Owner Mailing Address", "Owner Occupied",
    "Parcel ID", "Assessed Value", "Est. Equity", "Last Sale", "Registry Book/Page", "Registry Search",
    "Listing Link", "Legal Notice", "Found On", "First Seen", "Last Checked", "Team Notes", "Key",
]
# Columns people fill in by hand: a value typed in the sheet wins over an empty scraped one,
# and Team Notes is always kept as typed.
TRACKER_KEEP_IF_EMPTY = {"Mortgage Amount", "Mortgage Year", "Lender / Mortgagee", "1st or 2nd",
                         "Tax / Water / Sewer Liens", "Septic / Sewer", "Specs", "Est. Equity",
                         "Opening Bid"}
TRACKER_ALWAYS_KEEP = {"Team Notes"}
# The tracker's own placeholder guesses; anything else typed in these columns (e.g. septic
# details from a call) is kept even though the tracker always has a guess of its own.
SEPTIC_GUESSES = {"Likely SEPTIC - verify", "Verify w/ BOH"}
TRACKER_KEEP_UNLESS = {"Septic / Sewer": SEPTIC_GUESSES}

CALL_COLS = [
    "Priority", "Stage", "Auction Date", "Days Left", "Status", "Times Postponed", "Owner (Assessor)",
    "Borrower (Legal Notice)", "Property Address", "Town", "Owner Mailing Address", "Owner Occupied",
    "Lender / Mortgagee", "Mortgage Year", "Assessed Value", "Last Sale", "Auction Company",
    "Legal Notice", "Why Call", "Phone", "Called?", "Call Date", "Outcome", "Call Notes", "Key",
]
CALL_ALWAYS_KEEP = {"Called?", "Call Date", "Outcome", "Call Notes"}
CALL_KEEP_IF_EMPTY = {"Phone"}  # typed-in numbers win over PropStream's

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
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        state = json.load(f)
    # Re-key saved auctions with today's address rules, so a change in how addresses are
    # matched ("5th" = "Fifth") doesn't make an auction look removed and re-added.
    rekeyed = {}
    for key, a in state.get("auctions", {}).items():
        if not key.startswith("N-") and a.get("address") and a.get("town"):
            key = P.address_key(a["address"], a["town"])
        prev = rekeyed.get(key)
        if prev is None or (a.get("last_seen", "") > prev.get("last_seen", "")):
            rekeyed[key] = a
    state["auctions"] = rekeyed
    return state


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


def add_xome_bids(auctions, today, now_str):
    """Fill in opening/starting bids Xome posts for sales we already have."""
    status = {"Source": "Xome (bids only)", "Kind": "Bid prices", "URL": xome.URL, "Last Run (ET)": now_str}
    try:
        found = xome.bids(Http(), today)
    except Exception as e:
        log.warning("xome: %s", e)
        return {**status, "Result": "ERROR", "Listings In Our Towns": 0, "Note": f"{type(e).__name__}: {str(e)[:150]}"}
    used = 0
    for key, bid in found.items():
        if key in auctions and not auctions[key].get("opening_bid"):
            auctions[key]["opening_bid"] = bid
            used += 1
    return {**status, "Result": "OK", "Listings In Our Towns": used,
            "Note": f"Upcoming MA sales with a bid posted: {len(found)}; matched to our list: {used}"}


def sync_calendar(rows, cfg, today, now_str):
    """Mirror upcoming sales onto the Google Calendar, if one is set up."""
    cal = os.environ.get("GOOGLE_CALENDAR_ID") or cfg.get("calendar_id")
    if not cal:
        return None
    status = {"Source": "Google Calendar", "Kind": "Calendar sync", "URL": "https://calendar.google.com",
              "Last Run (ET)": now_str}
    try:
        res = calendar_sync.sync(rows, cal, cfg["spreadsheet_id"], today)
    except Exception as e:
        log.warning("calendar sync failed: %s", e)
        return {**status, "Result": "ERROR", "Listings In Our Towns": 0, "Note": f"{type(e).__name__}: {str(e)[:150]}"}
    return {**status, "Result": "OK", "Listings In Our Towns": res["events"],
            "Note": f"On calendar: {res['events']}; added {res['added']}, updated {res['updated']}, "
                    f"removed {res['removed']} (postponed or cancelled)"}


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
                owner = (a.get("gis") or {}).get("owner")
                if a["county"] == n["county"] and owner_matches(owner, pats):
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


STREET_WORDS = {"ST", "STREET", "RD", "ROAD", "AVE", "AV", "AVENUE", "DR", "DRIVE", "LN", "LANE", "CT", "COURT",
                "PL", "PLACE", "TER", "TERR", "TERRACE", "WAY", "CIR", "CIRCLE", "BLVD", "PKWY", "HWY", "EXT",
                "N", "S", "E", "W", "UNIT", "APT"}
LOCATE_QUERIES = 60  # notice-text searches allowed per notice, ever (results are saved)


class _OutOfQueries(Exception):
    pass


def _street_word(site_addr):
    """The most distinctive word of a street address: '22 SORRENTO ST' -> 'SORRENTO'."""
    words = [w for w in re.findall(r"[A-Z0-9']+", re.sub(r"#.*", "", site_addr.upper()))
             if w not in STREET_WORDS and not w.isdigit()]
    return max(words, key=len) if words else ""


def locate_by_notice_text(records, gis, state, today, towns, http_factory=Http):
    """Notice-only sales with a borrower but no address: narrow the borrower's possible
    properties (assessor parcels whose owner shares a last name) by full-text searching the
    notice for each candidate's town and then street. Each notice is worked out once; the
    answer (or that there wasn't one) is saved in the state file."""
    store = state.setdefault("notice_addresses", {})
    sessions = {}
    for key, a in records.items():
        n = (a.get("notices") or [{}])[0]
        if a.get("address") or not n.get("borrower") or not n.get("county"):
            continue
        saved = store.get(key)
        if saved and (saved.get("site_address") or saved.get("checked", "") >= (today - timedelta(days=14)).isoformat()):
            if saved.get("site_address"):
                a["gis"], a["address"], a["town"] = saved, saved["site_address"], saved["town"]
                _note_found_address(a)
            continue
        county_towns = [t for t in COUNTY_TOWNS[n["county"]] if t in towns]
        named = [t for t in n.get("probe_towns", []) if t in county_towns]
        cands = [r for r in gis.owner_candidates(n["borrower"], named or county_towns) if (r.get("RES_AREA") or 0) > 0]
        # Several owners with the borrower's full name: test just those first.
        pats = owner_patterns(n["borrower"])
        exact = [r for r in cands if owner_matches(r.get("OWNER1"), pats)]
        if 1 < len(exact) <= 8:
            cands = exact
        last = sorted(massgis_last_names(n["borrower"]))
        ids = set(n.get("ids", [n["notice_id"]]))
        if not cands or not last:
            store[key] = {"checked": today.isoformat()}
            continue
        if n["county"] not in sessions:
            sessions[n["county"]] = public_notices.NoticeSearch(http_factory(), [n["county"]])
        session, budget = sessions[n["county"]], [LOCATE_QUERIES]

        def says(word):
            """Does the notice's full text contain the borrower's last name and `word`?"""
            if budget[0] <= 0:
                raise _OutOfQueries
            budget[0] -= 1
            return bool(ids & session.ids(f"{last[0]} {word}", 60, mode="AND"))

        try:
            snippet = n.get("snippet", "").upper()
            by_town = {}
            for r in cands:
                by_town.setdefault(r["_town"], []).append(r)
            if len(by_town) > 1 and len({_street_word(str(r.get("SITE_ADDR"))) for r in cands}) > 12:
                # A town named in the notice's opening (a lender, the registry) proves nothing.
                testable = [t for t in by_town if t.upper() not in snippet and t != n["county"]]
                hit = [t for t in sorted(testable, key=lambda t: -len(by_town[t])) if says(t)]
                cands = [r for t in (hit or [t for t in by_town if t not in testable]) for r in by_town[t]]
            by_street = {}
            for r in cands:
                by_street.setdefault((r["_town"], _street_word(str(r.get("SITE_ADDR")))), []).append(r)
            if len(by_street) > budget[0]:
                raise _OutOfQueries
            found = [r for (t, w), rs in by_street.items() if w and says(w) for r in rs]
            if len(found) > 1:
                found = [r for r in found if (m := re.match(r"\d+", str(r.get("SITE_ADDR") or ""))) and says(m.group())]
        except _OutOfQueries:
            found = []
        except Exception as e:
            log.warning("locating %s: %s", n["borrower"], e)
            continue  # try again next run
        if len(found) == 1:
            res = gis.summarize(found[0])
            res.update({"town": found[0]["_town"], "matched_by": "borrower name + notice text",
                        "checked": today.isoformat()})
            store[key] = res
            a["gis"], a["address"], a["town"] = res, res["site_address"], res["town"]
            _note_found_address(a)
            log.info("located %s: %s, %s", n["borrower"], res["site_address"], res["town"])
        else:
            store[key] = {"checked": today.isoformat()}


def _note_found_address(a):
    note = "Address found from the borrower's name in assessor records - confirm with the legal notice"
    a["status_note"] = "; ".join(x for x in (a.get("status_note"), note) if x)


def use_typed_addresses(notice_only, typed, towns, previous):
    """An address someone typed over a notice-only row's "(borrower: ...)" placeholder is
    kept, and used to look the property up like any other address. An address the tracker
    wrote there itself (found from the borrower's name) is not "typed"."""
    by_key = {r.get("Key"): r for r in typed if r.get("Key")}
    for key, a in notice_only.items():
        r = by_key.get(key) or {}
        addr, town = str(r.get("Address") or "").strip(), str(r.get("Town") or "").strip()
        ours = str((previous.get(key) or {}).get("address") or "").strip()
        if a.get("address") or not addr or addr.startswith("(") or addr.upper() == ours.upper():
            continue
        a["address"] = addr
        if town in towns:
            a["town"] = town


def _sale_spot(a):
    m = re.match(r"\s*(\d+)", a.get("address") or "")
    word = _street_word(re.sub(r"^[\d\s-]+", "", (a.get("address") or "").upper()))
    if not (m and word and a.get("town") and a.get("auction_date")):
        return None
    return (a["town"], a["auction_date"], m.group(1), word)


def fold_into_listings(auctions, notice_only):
    """A notice-only sale whose address we just found may be a listing we already have
    (an auctioneer's site writes the address differently). Attach it there instead."""
    by_parcel = {(a.get("gis") or {}).get("parcel_id"): k for k, a in auctions.items() if (a.get("gis") or {}).get("parcel_id")}
    by_addr = {P.address_key(a["address"], a["town"]): k for k, a in auctions.items() if a.get("address")}
    # Same town, sale date, house number and street word ("11-3 Rice Lane" vs "11 Rice Ln #3").
    by_sale = {_sale_spot(a): k for k, a in auctions.items() if _sale_spot(a)}
    out = {}
    for key, a in notice_only.items():
        g = a.get("gis") or {}
        k = (by_parcel.get(g.get("parcel_id"))
             or (a.get("address") and by_addr.get(P.address_key(a["address"], a["town"])))
             or by_sale.get(_sale_spot(a)))
        if k:
            auctions[k].setdefault("notices", []).extend(a["notices"])
        else:
            out[key] = a
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
                _note_found_address(a)
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
        "Opening Bid": a.get("opening_bid", ""),
        "Auction Company": (a.get("auction_company", "") or n.get("auction_company", "")
                            or ", ".join(s for s in a.get("sources", []) if s != "Legal notice")
                            or ("From Public Notice" if notices else "")),
        "Law Firm (Notice)": n.get("law_firm", ""),
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


def keep_sheet_edits(rows, existing, always, if_empty, unless=None):
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
        for col, guesses in (unless or {}).items():
            typed = str(o.get(col) or "").strip()
            if typed and typed not in guesses:
                r[col] = typed
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
        already_held = bool(a.get("auction_date")) and a["auction_date"] < today.isoformat()
        if not p:
            # Notices found by a backfill can describe sales that already happened; those
            # aren't news, so only announce auctions still to come.
            if not first_run and not already_held:
                event("New legal notice (not on auction sites)" if a["status"] == "Notice only" else "New auction",
                      key, a, "", f"{_us(a['auction_date'])} {a.get('auction_time', '')} {a['status']}".strip())
            a["times_postponed"] = 1 if a["status"] == "Postponed" else 0
            a["first_seen"] = now_str
        else:
            a["first_seen"] = p.get("first_seen", now_str)
            # Bid sites take a sale down once it's held; keep the last bid we saw.
            if not a.get("opening_bid") and p.get("opening_bid"):
                a["opening_bid"] = p["opening_bid"]
            a["times_postponed"] = p.get("times_postponed", 0)
            if p.get("status") != a["status"]:
                event(f"Status: {p.get('status')} -> {a['status']}", key, a, p.get("status", ""), a["status"])
            if (p.get("auction_date") and a["auction_date"] and p["auction_date"] != a["auction_date"]
                    and not already_held):
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
        if r["Status"] in ("Cancelled", "Sold", "Removed from site", "Held"):
            continue
        if r["Address"].startswith(MUNICIPAL_PREFIX):
            continue  # a town's own sale: no homeowner to call
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
    version = 3
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


INACTIVE = ("Cancelled", "Postponed", "Sold", "Removed from site", "Held")


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
    health.append(add_xome_bids(auctions, today, now_str))
    enrich(auctions, gis, cfg["town_set"])  # assessor data first, so owner-name matching works
    sale_notices = [n for n in notices if n["kind"] == "Mortgagee's sale"]
    land_court = [n for n in notices if n["kind"] == "Land Court filing"]
    if notice_health["Result"] == "OK":
        public_notices.apply_probe(sale_notices, notice_facts(cfg, state, today))
    unmatched = match_notices(auctions, sale_notices)
    notice_only = notice_only_records(unmatched, today)
    tabs = cfg["tabs"]
    # Rows move between the two tabs as their status changes, so look for typed-in notes on both.
    typed = writer.read_rows(tabs["tracker"]) + writer.read_rows(tabs["cancelled"])
    use_typed_addresses(notice_only, typed, cfg["town_set"], state.get("auctions", {}))
    enrich(notice_only, gis, cfg["town_set"])
    locate_by_notice_text(notice_only, gis, state, today, cfg["town_set"])
    notice_only = fold_into_listings(auctions, notice_only)
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
        if not a.get("auction_date") and key not in auctions:
            continue  # undated and no longer found: an address that wasn't a sale
        row = build_row(key, a, cfg)
        if row["Status"] in ("Scheduled", "Notice only") and a.get("auction_date") and a["auction_date"] < today.isoformat():
            row["Status"] = "Held"  # the date has passed; results aren't posted
            row["Status Notes"] = row["Status Notes"] or "Sale date has passed - check the result with the auctioneer"
        rows.append(row)
    rows.sort(key=lambda r: (r["Auction Date"][6:] + r["Auction Date"][:5] if r["Auction Date"] else "9999",
                             _minutes(r["Time"]), r["Town"]))

    rows = keep_sheet_edits(rows, typed, TRACKER_ALWAYS_KEEP, TRACKER_KEEP_IF_EMPTY, TRACKER_KEEP_UNLESS)
    export = writer.read_rows(tabs["propstream_export"])
    rows = propstream.apply(rows, export)
    writer.write_table(tabs["tracker"], TRACKER_COLS, [r for r in rows if r["Status"] not in INACTIVE],
                       status_col="Status")
    writer.write_table(tabs["cancelled"], TRACKER_COLS, [r for r in rows if r["Status"] in INACTIVE],
                       status_col="Status")
    calls = call_rows(rows, all_auctions, land_court, cfg, today)
    calls = keep_sheet_edits(calls, writer.read_rows(tabs["call_list"]), CALL_ALWAYS_KEEP, CALL_KEEP_IF_EMPTY)
    calls = propstream.apply_phones(calls, export)
    lookup = propstream.lookup_rows(rows, today)
    writer.write_table(tabs["propstream_lookup"], propstream.LOOKUP_COLS,
                       lookup + propstream.call_lookup_rows(calls, lookup))
    writer.write_table(tabs["call_list"], CALL_COLS, calls, status_col="Status")
    writer.append_rows(tabs["changes"], CHANGE_COLS, events)
    # The calendar is built from the tabs as written, the same input the hourly refresh
    # (calendar_sync.py) reads, so the two never disagree about an event.
    written = writer.read_rows(tabs["tracker"]) + writer.read_rows(tabs["cancelled"])
    health.append(sync_calendar(written, cfg, today, now_str))
    writer.write_table(tabs["sources"], SOURCE_COLS, [h for h in health if h])

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

"""Scrape auctioneer websites for foreclosure auctions in our towns.

A few big auctioneers publish their schedule as JSON or a clean table and get a
dedicated parser. Everything else goes through `generic`, which finds each listing's
block on the page (the element that holds exactly one street address) and pulls the
date, time, deposit and status out of that block's text.
"""

import json
import logging
import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import parse as P
from towns import resolve_town

log = logging.getLogger(__name__)

NEW_DATE_RE = re.compile(r"(postponed|continued|rescheduled|adjourned|moved)\s*(to|until|till)\b|new (auction )?date", re.I)
OLD_DATE_RE = re.compile(r"previously scheduled( for)?|originally scheduled( for)?|was scheduled", re.I)


def build_record(company, url, address, town, county, text, today, detail_url=""):
    """Turn one listing's text block into a record."""
    text = P.clean(text)
    dates = P.all_dates(text, today)
    status = P.parse_status(text)
    current, original = None, None
    new_m, old_m = NEW_DATE_RE.search(text), OLD_DATE_RE.search(text)
    if new_m:
        after = [d for pos, d in dates if pos > new_m.start()]
        before = [d for pos, d in dates if pos < new_m.start()]
        current = after[0] if after else None
        original = before[0] if before else None
        status = status if status == "Cancelled" else "Postponed"
    elif old_m:
        after = [d for pos, d in dates if pos > old_m.start()]
        before = [d for pos, d in dates if pos < old_m.start()]
        original = after[0] if after else None
        current = before[0] if before else None
        if status != "Cancelled":
            status = "Postponed"
    distinct = sorted({d for _, d in dates})
    if not current and distinct:
        current = distinct[0]
        if len(distinct) > 1 and status == "Postponed":
            original, current = distinct[0], distinct[-1]
    time_text = text
    if current:
        for pos, d in dates:
            if d == current:
                time_text = text[pos:pos + 60]
                break
    return {
        "address": address,
        "town": town,
        "county": county,
        "auction_date": current.isoformat() if current else "",
        "original_date": original.isoformat() if original and original != current else "",
        "auction_time": P.parse_time(time_text) or P.parse_time(text),
        "status": status or "Scheduled",
        "deposit": P.parse_deposit(text) or _bare_deposit(text),
        "auction_company": company,
        "book_page": P.parse_book_page(text),
        "specs": P.parse_specs(text),
        "sewer": P.parse_sewer(text),
        "listing_text": text[:600],
        "source": company,
        "source_url": url,
        "detail_url": detail_url,
    }


def _bare_deposit(text):
    """Some tables have a deposit column with no 'deposit' label: take a $5,000-$100,000 figure."""
    for m in P.MONEY_RE.finditer(re.sub(r"\$\s*\|\s*", "$", text)):
        v = float(m.group(1).replace(",", ""))
        if 1000 <= v <= 100000 and v % 500 == 0:
            return P.fmt_money(v)
    return ""


# ---------------------------------------------------------------- dedicated parsers

def commonwealth(http, site, today):
    """Commonwealth Auctions (Harmon Law's auctioneer): JSON behind a DataTable."""
    base = "https://www.commonwealthauctions.com/"
    r = http.post(base + "Components/Auction/Assets/AJAX/ListAuctions.php?Category=1",
                  headers={"X-Requested-With": "XMLHttpRequest", "Referer": base + "ma-auctions"})
    rows = r.json().get("data", [])
    out = []
    for row in rows:
        if row.get("State") != "MA":
            continue
        # "158 Applewood Drive| Doverbrook Estates Condominium Chicopee": street before the
        # bar, town at the very end.
        raw = row.get("Location") or ""
        town, county, street = _split_trailing_town(P.clean(raw.replace("|", " ")))
        if not town:
            continue
        if "|" in raw:
            street = P.clean(raw.split("|")[0])
        text = f"{row.get('Date', '')} {row.get('Status', '')} deposit {row.get('Deposit', '')}"
        rec = build_record(site["name"], site["url"], street, town, county, text, today)
        rec["status"] = _norm_status(row.get("Status")) or rec["status"]
        href = re.search(r"href='([^']+)'", row.get("Links", ""))
        rec["detail_url"] = urljoin(base, href.group(1)) if href else ""
        out.append(rec)
    return out


def baystate(http, site, today):
    """Bay State Auction: listings embedded as a JS array (`var Auctions = [...]`)."""
    r = http.get("https://www.baystateauction.com/auctions/state/ma")
    m = re.search(r"var Auctions\s*=\s*(\[.*?\])\s*;?\s*</script>", r.text, re.S)
    if not m:
        raise ValueError("Auctions array not found")
    # The array is JS, not strict JSON (trailing commas); tidy it up.
    raw = re.sub(r",\s*([\]}])", r"\1", m.group(1))
    items = json.loads(raw)
    out = []
    for it in items:
        if (it.get("state") or "").strip() != "MA" or it.get("past"):
            continue
        town, county = resolve_town(it.get("city"))
        if not town:
            continue
        date_html = BeautifulSoup(it.get("date", ""), "lxml").get_text(" ")
        text = f"{date_html} | {it.get('text', '')} | deposit {it.get('deposit', '')}"
        rec = build_record(site["name"], site["url"], P.clean(it.get("address")), town, county, text, today,
                           detail_url=f"https://www.baystateauction.com/auctions/{it.get('id')}")
        rec["deposit"] = P.fmt_money(P.parse_money(it.get("deposit"))) or rec["deposit"]
        out.append(rec)
    return out


def towne(http, site, today):
    """Towne Auction: one HTML table with a status and a county column."""
    r = http.get("https://currentauctions.towneauction.com")
    soup = BeautifulSoup(r.text, "lxml")
    out = []
    for tr in soup.find_all("tr"):
        cells = [P.clean(td.get_text(" ")) for td in tr.find_all("td")]
        if len(cells) < 8 or cells[5] != "MA":
            continue
        # Auction Date, Time, Status, Address, City, State, Zip, County, Page/Liber, Deposit, Info
        town, county = resolve_town(cells[4])
        if not town:
            continue
        text = " | ".join(cells)
        rec = build_record(site["name"], site["url"], cells[3], town, county, text, today)
        rec["status"] = _norm_status(cells[2]) or rec["status"]
        rec["auction_time"] = P.parse_time(cells[1]) or rec["auction_time"]
        d = P.parse_date(cells[0], today)
        rec["auction_date"] = d.isoformat() if d else rec["auction_date"]
        if len(cells) > 9:
            rec["deposit"] = P.fmt_money(P.parse_money(cells[9])) or rec["deposit"]
        out.append(rec)
    return out


def servicelink(http, site, today):
    """ServiceLink Auction: its site is a JavaScript app, but it reads a public JSON feed."""
    r = http.get("https://www.servicelinkauction.com/api/listingsvc/v1/listings?state=MA&limit=100",
                 headers={"Accept": "application/json"})
    out = []
    for it in r.json().get("data", []):
        if it.get("listingProgramWebsite") != "Foreclosure Sale":
            continue  # skip bank-owned and post-foreclosure resales
        prop = it.get("propertyInfo") or {}
        town, county = resolve_town(prop.get("city"))
        if not town:
            continue
        status_text = f"{it.get('foreclosureSaleStatusWebsite', '')} {it.get('foreclosureSaleStatus', '')}"
        sale = (it.get("foreclosureSaleDate") or "")[:10]
        specs = ", ".join(x for x in [
            prop.get("propertyType", ""),
            f"{int(prop['bedrooms'])} beds" if prop.get("bedrooms") else "",
            f"{prop['fullBathrooms']:g} baths" if prop.get("fullBathrooms") else "",
        ] if x)
        rec = build_record(site["name"], site["url"], P.clean(prop.get("address")), town, county,
                           f"{status_text} {it.get('tpsSaleTime', '')}", today,
                           detail_url=prop.get("websiteUrl", ""))
        rec.update({
            "auction_date": sale or rec["auction_date"],
            "auction_time": P.parse_time(it.get("tpsSaleTime") or "") or rec["auction_time"],
            "status": _norm_status(status_text) or "Scheduled",
            "specs": specs,
            "opening_bid": (P.fmt_money(it.get("tpsOpenBid") or it.get("openingBid")) + " (ServiceLink)"
                            if (it.get("tpsOpenBid") or it.get("openingBid")) else ""),
        })
        out.append(rec)
    return out


WEEKDAY_RE = re.compile(r"\b(MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY)\b")
PLEASE_SEE_RE = re.compile(r"please\s+see", re.I)


def posnik(http, site, today):
    """Aaron Posnik real-estate calendar: one table row per sale, cells 'STATUS? DAY DATE
    [AT TIME] [Please See NEW DATE]' | 'TOWN, MA' | description and street address.
    The status sits above the weekday in the date cell, so each row is read on its own;
    reading the page as one run of text hands every status to the sale before it."""
    r = http.get(site["url"])
    soup = BeautifulSoup(r.text, "lxml")
    for t in soup(["script", "style"]):
        t.decompose()
    # Each sale has its own page named after its first scheduled date (html26/110626.htm).
    pages = {a["href"].rsplit("/", 1)[-1]: urljoin(r.url, a["href"])
             for a in soup.find_all("a", href=True) if re.search(r"/\d{6}\.htm$", a["href"])}
    out = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 3 or not WEEKDAY_RE.search(cells[0].get_text(" ").upper()):
            continue
        seg = " | ".join(c.get_text(" | ", strip=True) for c in cells)
        tm = re.search(r"\b(" + "|".join(re.escape(x) for x in P.all_place_names()) + r")\s*,\s*MA\b",
                       cells[1].get_text(" ", strip=True), re.I)
        am = P.ADDRESS_RE.search(" | ".join(c.get_text(" | ", strip=True) for c in cells[2:]))
        if not tm or not am:
            continue
        town, county = resolve_town(tm.group(1))
        if not town:
            continue
        addr = P.clean(am.group(1))
        dates = [d for _, d in P.all_dates(cells[0].get_text(" ", strip=True), today)]
        link = cells[0].find("a", href=True)
        detail = (urljoin(r.url, link["href"]) if link and re.search(r"/\d{6}\.htm$", link["href"])
                  else next((pages[f"{d:%m%d%y}.htm"] for d in dates if f"{d:%m%d%y}.htm" in pages), ""))
        rec = build_record(site["name"], site["url"], addr, town, county, seg, today, detail)
        if dates:
            rec["auction_date"] = dates[0].isoformat()
        if PLEASE_SEE_RE.search(seg) and len(dates) > 1:
            rec["original_date"], rec["auction_date"] = dates[0].isoformat(), dates[1].isoformat()
            if rec["status"] != "Cancelled":
                rec["status"] = "Postponed"
        out.append(rec)
    return out


MUNICIPAL_PREFIX = "(municipal auction"
MUNICIPAL_RE = re.compile(r"^(?:City|Town) of\s+(.+?)\s*(?:Coming Soon!?)?$", re.I)


def zekos(http, site, today):
    """The Zekos Group: every sale is a block on the home page (title, a badge such as
    "5 PROPERTIES" or "SOLD", "October 29 at 2:00 PM EDT"). Lender foreclosures are titled
    with the address. Municipal (town-owned / tax title) sales are titled "City of X" and
    hold several properties; each property gets its own row, read from the sale's
    "Auction Properties" list. Its robots.txt asks automated visitors to skip /ajax, where
    that list lives; the owner chose to read it anyway, at the 10-second pace the file
    asks for, a few times a day. If the list can't be read, the sale stays one row."""
    r = http.get(site["url"])
    soup = BeautifulSoup(r.text, "lxml")
    out = []
    for h3 in soup.find_all("h3"):
        a = h3.find("a", href=re.compile(r"/auctions/detail/"))
        if not a:
            continue
        block = h3.find_parent(class_="row") or h3.parent
        badge = block.find(class_="easy-sold-badge")
        badge = badge.get_text(" ", strip=True) if badge else ""
        title = P.clean(a.get_text(" ", strip=True))
        text = P.clean(block.get_text(" | ", strip=True))
        detail = urljoin(r.url, a["href"])
        m = MUNICIPAL_RE.match(title)
        if m:
            town, county = resolve_town(m.group(1))
            if not town:
                continue
            rec = build_record(site["name"], site["url"], f"{MUNICIPAL_PREFIX} - {title.split(' Coming')[0]})",
                               town, county, text, today, detail)
            count = re.search(r"(\d+)\s+(PROPERTIES|TOWNHOUSES|PARCELS|LOTS|HOMES|UNITS)", badge, re.I)
            rec["specs"] = (f"{count.group(1)} town-owned {count.group(2).lower()}" if count
                            else "Town-owned property") + " - see the auctioneer's page for the list"
            rec["status_note"] = "Municipal (town-owned / tax title) auction"
            rec["skip_detail"] = True
            if rec["auction_date"] and rec["auction_date"] >= today.isoformat():
                _municipal_details(http, rec)
                props = _municipal_properties(http, r.url, a["href"], rec)
                if props:
                    out += props
                    continue
        else:
            town, county, street = _split_trailing_town(re.sub(r",?\s*MA\b\.?$", "", title).replace(",", " "))
            if not town:
                continue
            rec = build_record(site["name"], site["url"], P.clean(street), town, county, text, today, detail)
        if badge.upper() == "SOLD":
            rec["status"] = "Sold"
        out.append(rec)
    return out


def _municipal_details(http, rec):
    """Deposit and where the sale is held, from the sale's own page (allowed by robots.txt,
    which asks for 10 seconds between requests)."""
    import time
    time.sleep(10)
    try:
        soup = BeautifulSoup(http.get(rec["detail_url"]).text, "lxml")
    except Exception as e:
        log.info("zekos detail %s: %s", rec["detail_url"], e)
        return
    text = P.clean(soup.get_text(" "))
    dep = re.search(r"\$\s?([\d,]+)\s+deposit\s+per\s+property", text, re.I)
    if dep:
        rec["deposit"] = f"${dep.group(1)} per property"
    where = re.search(r"will be held on [^.]*?\d{1,2}:\d\d\s*[AP]\.?M\.?\s+at\s+(.{5,90}?,\s*MA)\b", text, re.I)
    if where:
        rec["status_note"] += f", held at {where.group(1)}"


def _municipal_properties(http, base_url, detail_href, sale):
    """One record per property in a municipal sale, from its "Auction Properties" list."""
    import time
    m = re.search(r"bw(\d+)", detail_href)
    if not m:
        return []
    time.sleep(10)
    try:
        soup = BeautifulSoup(http.get(urljoin(base_url, f"/ajax/items/{m.group(1)}/undefined"),
                                      headers={"X-Requested-With": "XMLHttpRequest"}).text, "lxml")
    except Exception as e:
        log.info("zekos items %s: %s", detail_href, e)
        return []
    out, seen = [], set()
    for a in soup.find_all("a", href=re.compile(r"/auctions/item/"), title=True):
        href = a["href"]
        if href in seen:
            continue
        seen.add(href)
        row = a.find_parent(class_="row") or a.parent
        blurb = P.clean(row.get_text(" ", strip=True).replace("View Property Info", "").replace("»", ""))
        addr = P.clean(a["title"])
        rec = dict(sale, address=addr, detail_url=urljoin(base_url, href), listing_text=blurb[:600],
                   specs=blurb.replace(addr, "").split(".")[0].strip(" .|") or "Town-owned property",
                   status_note=sale["status_note"].replace("auction", f"auction ({len(seen)} of many)", 1))
        dep = re.search(r"Deposit\s+\$([\d,]+)", blurb, re.I)
        if dep:
            rec["deposit"] = f"${dep.group(1)}"
        out.append(rec)
    for rec in out:  # "(3 of many)" -> "(3 of 5)"
        rec["status_note"] = rec["status_note"].replace("of many)", f"of {len(out)})")
    return out


def _split_trailing_town(loc):
    """'62 Huntington Avenue West Springfield' -> ('West Springfield', 'Hampden', '62 Huntington Avenue')."""
    words = loc.split()
    for n in (3, 2, 1):
        if len(words) > n:
            town, county = resolve_town(" ".join(words[-n:]))
            if town:
                return town, county, " ".join(words[:-n]).rstrip(",")
    return None, None, loc


def _norm_status(s):
    s = (s or "").lower()
    if "cancel" in s:
        return "Cancelled"
    if "postpon" in s or "continu" in s:
        return "Postponed"
    if "sold" in s:
        return "Sold"
    if any(k in s for k in ("active", "on_time", "on time", "schedule")):
        return "Scheduled"
    return ""


# ---------------------------------------------------------------- generic parser

BLOCK_TAGS = ["tr", "li", "article", "div", "section", "p", "td", "dd", "table", "a"]


def _addr_keys(text):
    return {P.address_key(m.group(1), "") for m in P.ADDRESS_RE.finditer(text)}


def generic(http, site, today):
    r = http.get(site["url"])
    soup = BeautifulSoup(r.text, "lxml")
    for t in soup(["script", "style", "noscript", "nav"]):
        t.decompose()
    base_url = r.url

    page_text = soup.get_text(" | ", strip=True)
    out, seen = [], set()
    candidates = soup.find_all(BLOCK_TAGS)
    counts = {}
    for el in candidates:
        counts[id(el)] = len(_addr_keys(el.get_text(" | ", strip=True)))

    # A listing block: holds exactly one distinct address, and its parent holds more.
    for el in candidates:
        if counts.get(id(el)) != 1:
            continue
        parent = el.find_parent(BLOCK_TAGS + ["tbody", "ul", "body"])
        parent_count = counts.get(id(parent)) if parent is not None else None
        if parent_count is None and parent is not None:
            parent_count = len(_addr_keys(parent.get_text(" | ", strip=True)))
        if parent is not None and parent_count == 1:
            continue  # a bigger block still holds just this listing; use that one instead
        text = el.get_text(" | ", strip=True)
        for start, end, addr, town, county in P.find_addresses(text):
            key = P.address_key(addr, town)
            if key in seen:
                continue
            seen.add(key)
            detail = _listing_link(el, base_url)
            rec = build_record(site["name"], site["url"], addr, town, county, text, today, detail)
            if not rec["auction_date"]:
                rec = _from_page_text(page_text, addr, town, county, site, today, detail) or rec
            if rec["auction_date"]:  # no date means an office or attorney address, not a sale
                out.append(rec)

    # Leaf text with several listings run together (e.g. one <p> per page): split on addresses.
    for el in candidates:
        if counts.get(id(el), 0) < 2:
            continue
        if any(counts.get(id(c), 0) >= 1 for c in el.find_all(BLOCK_TAGS)):
            continue
        text = el.get_text(" | ", strip=True)
        hits = P.find_addresses(text)
        all_starts = sorted(m.start() for m in P.ADDRESS_RE.finditer(text))
        for start, end, addr, town, county in hits:
            key = P.address_key(addr, town)
            if key in seen:
                continue
            seen.add(key)
            nxt = next((s for s in all_starts if s > start), len(text))
            rec = build_record(site["name"], site["url"], addr, town, county, text[start:nxt], today)
            if not rec["auction_date"]:
                rec = _from_page_text(page_text, addr, town, county, site, today, "") or rec
            if rec["auction_date"]:  # no date means an office or attorney address, not a sale
                out.append(rec)
    return out


SKIP_LINK = re.compile(r"\.(jpe?g|png|gif|webp|pdf)(\?|$)|maps\.google|goo\.gl|google\.com/maps|mailto:|tel:", re.I)


def _listing_link(el, base_url):
    """The block's link to its own listing page, skipping photos, maps and phone links."""
    links = ([el] if el.name == "a" and el.get("href") else []) + el.find_all("a", href=True)
    parent = el.find_parent("a", href=True)
    if parent is not None:
        links.append(parent)
    for a in links:
        if not SKIP_LINK.search(a["href"]) and not a["href"].startswith("#"):
            return urljoin(base_url, a["href"])
    return ""


def _from_page_text(page_text, addr, town, county, site, today, detail):
    """For flat layouts (address and date in sibling elements): read from this address
    to the next one, or back to the previous one when the date comes first."""
    pos = page_text.find(addr)
    if pos < 0:
        return None
    starts = sorted(m.start() for m in P.ADDRESS_RE.finditer(page_text))
    nxt = next((x for x in starts if x > pos), len(page_text))
    rec = build_record(site["name"], site["url"], addr, town, county, page_text[pos:nxt][:1200], today, detail)
    if rec["auction_date"]:
        return rec
    # Date before the address: start from the closest date in the 300 characters before it.
    window_start = max(0, pos - 300)
    before = P.all_dates(page_text[window_start:pos], today)
    if before:
        seg = page_text[window_start + before[-1][0]: nxt][:1200]
        rec = build_record(site["name"], site["url"], addr, town, county, seg, today, detail)
        if rec["auction_date"]:
            return rec
    return None


PARSERS = {"commonwealth": commonwealth, "baystate": baystate, "towne": towne, "posnik": posnik,
           "servicelink": servicelink, "zekos": zekos}


def scrape_site(http, site, today=None, towns=None):
    today = today or date.today()
    fn = PARSERS.get(site.get("parser", "generic"), generic)
    recs = fn(http, site, today)
    if towns is not None:
        recs = [r for r in recs if r["town"] in towns]
    add_detail_pages(http, recs, today)
    return recs


def add_detail_pages(http, recs, today):
    """Many schedule pages leave the deposit (and Book/Page) to each listing's own page.
    Read that page for upcoming listings that are missing them."""
    for rec in recs:
        url = rec.get("detail_url")
        stale = rec["auction_date"] and rec["auction_date"] < today.isoformat()
        if (not url or url == rec["source_url"] or "#" in url or rec["status"] in ("Cancelled", "Sold")
                or rec.get("skip_detail")
                or (stale and rec["status"] != "Postponed")
                or (rec["deposit"] and rec["book_page"] and rec["status"] != "Postponed")):
            continue
        try:
            soup = BeautifulSoup(http.get(url).text, "lxml")
        except Exception as e:
            log.info("detail page %s: %s", url, e)
            continue
        for t in soup(["script", "style", "nav", "footer"]):
            t.decompose()
        text = P.clean(soup.get_text(" "))
        num, street, _ = P.split_address(rec["address"])
        if street and street.split()[0] not in text.upper():
            continue  # a shared page that isn't about this property
        # Some auctioneers (Posnik) mark the sale "CANCELLED" on its own page first.
        if re.search(r"\|\s*CANCELL?ED\s*\|", P.clean(soup.get_text(" | "))):
            rec["status"] = "Cancelled"
        rec["deposit"] = rec["deposit"] or P.parse_deposit(text) or ""
        rec["book_page"] = rec["book_page"] or P.parse_book_page(text)
        rec["specs"] = rec["specs"] or P.parse_specs(text)
        rec["sewer"] = rec["sewer"] or P.parse_sewer(text)
        rec["listing_text"] = (rec["listing_text"] + " | " + text[:1500])[:2000]

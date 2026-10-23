"""Text helpers shared by every source: addresses, dates, times, money, status."""

import hashlib
import re
from datetime import date, datetime

from dateutil import parser as dateparser

from towns import all_place_names, resolve_town

STREET_TYPES = (
    "Street|St|Road|Rd|Avenue|Ave|Av|Drive|Dr|Lane|Ln|Circle|Cir|Court|Ct|Place|Pl|Way|"
    "Terrace|Ter|Terr|Boulevard|Blvd|Highway|Hwy|Pike|Turnpike|Tpke|Tpk|Parkway|Pkwy|"
    "Square|Sq|Trail|Trl|Path|Row|Hill|Heights|Hts|Extension|Ext|Crossing|Xing|Run|"
    "Point|Pt|Park|Common|Commons|Green|Walk|Alley|Loop|Ridge|Rdg|Landing|Glen|Hollow|"
    "Meadow|Meadows|Brook|Village|Center|Ctr|Estates"
)
ADDRESS_RE = re.compile(
    r"\b(\d{1,6}[A-Za-z]?(?:\s*[-–&/]\s*\d{1,6}[A-Za-z]?)?"            # house number(s)
    r"(?:\s+(?:North|South|East|West|N|S|E|W)\.?)?"
    r"(?:\s+[A-Z0-9][\w'’.-]*){1,5}?\s+"                                # street name words
    r"(?:" + STREET_TYPES + r")\b\.?"
    r"(?:\s*(?:,\s*)?(?:Unit|Apt|Apartment|#|Suite|Ste)\.?\s*[\w-]+)?)",
    re.IGNORECASE,
)

_PLACES = all_place_names()
PLACE_RE = re.compile(r"\b(" + "|".join(re.escape(p) for p in _PLACES) + r")\b", re.IGNORECASE)

MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
DATE_RE = re.compile(
    r"\b(?:(?:Mon|Tue|Tues|Wed|Thu|Thur|Thurs|Fri|Sat|Sun)[a-z]*\.?,?\s+)?"
    r"(" + MONTHS + r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\d{1,2}-\d{1,2}-\d{2,4})\b",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*([AaPp])\.?\s*[Mm]\.?", re.IGNORECASE)
MONEY_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)\s*(k|K|thousand)?")
DEPOSIT_RE = re.compile(r"deposit[^$]{0,40}\$\s?([\d,]+(?:\.\d{2})?)", re.IGNORECASE)
BOOK_PAGE_RE = re.compile(r"\b(?:Book|Bk|B)\.?\s*:?\s*(\d{2,6})\s*[,/]?\s*(?:at\s+)?(?:Page|Pg|P)\.?\s*:?\s*(\d{1,5})", re.IGNORECASE)

STATUS_PATTERNS = [
    ("Cancelled", re.compile(r"\b(cancel+ed|canceled|cancelled|withdrawn|removed)\b", re.I)),
    ("Postponed", re.compile(r"\b(postponed|continued|rescheduled|adjourned|previously scheduled)\b", re.I)),
    ("Sold", re.compile(r"\b(sold|third party sale)\b", re.I)),
    ("Scheduled", re.compile(r"\b(on[_ ]time|on schedule|as scheduled|scheduled|active|upcoming)\b", re.I)),
]

SEPTIC_RE = re.compile(r"\b(septic|title\s*5|title\s*v|private\s+sewage)\b", re.I)
SEWER_RE = re.compile(r"\b(town|city|municipal|public)\s+sewer\b", re.I)
BEDS_RE = re.compile(r"(\d+(?:\.\d)?)\s*(?:bed(?:room)?s?|bdrms?|br)\b", re.I)
BATHS_RE = re.compile(r"(\d+(?:\.\d)?)\s*(?:full\s+)?(?:bath(?:room)?s?|bths?|ba)\b", re.I)
PROPERTY_TYPE_RE = re.compile(
    r"\b(single[\s-]family|sgl\s+fam|two[\s-]family|2[\s-]family|three[\s-]family|3[\s-]family|"
    r"multi[\s-]family|condo(?:minium)?|mixed[\s-]use|commercial|vacant\s+land|land|"
    r"mobile\s+home|manufactured\s+home|ranch|cape|colonial|duplex|raised\s+ranch|split[\s-]level)\b",
    re.I,
)


def clean(text):
    return " ".join((text or "").replace("\xa0", " ").split())


def find_place(text):
    """First recognised town/village in text -> (town, county)."""
    for m in PLACE_RE.finditer(text or ""):
        town, county = resolve_town(m.group(1))
        if town:
            return town, county
    return None, None


def parse_date(text, today=None):
    """Parse the first date in text. Dates without a year get the next sensible year."""
    today = today or date.today()
    m = DATE_RE.search(text or "")
    if not m:
        return None
    raw = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", m.group(1))
    raw = raw.replace("Sept", "Sep")
    try:
        default = datetime(today.year, 1, 1)
        d = dateparser.parse(raw, default=default, fuzzy=True).date()
    except (ValueError, OverflowError):
        return None
    has_year = bool(re.search(r"\d{4}|/\d{2}$|-\d{2}$", raw))
    if not has_year and (today - d).days > 120:
        d = d.replace(year=d.year + 1)
    return d


def all_dates(text, today=None):
    out = []
    for m in DATE_RE.finditer(text or ""):
        d = parse_date(m.group(0), today)
        if d:
            out.append((m.start(), d))
    return out


def parse_time(text):
    m = TIME_RE.search(text or "")
    if not m:
        return ""
    hour, minute, ampm = int(m.group(1)), m.group(2) or "00", m.group(3).upper()
    if not 1 <= hour <= 12:
        return ""
    return f"{hour}:{minute} {ampm}M"


def parse_money(text):
    m = MONEY_RE.search(text or "")
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    if m.group(2):
        value *= 1000
    return value


def fmt_money(value):
    if value in (None, ""):
        return ""
    return f"${value:,.0f}"


def parse_deposit(text):
    m = DEPOSIT_RE.search(text or "")
    if m:
        return fmt_money(float(m.group(1).replace(",", "")))
    return ""


def parse_status(text):
    for status, rx in STATUS_PATTERNS:
        if rx.search(text or ""):
            return status
    return ""


def parse_book_page(text):
    m = BOOK_PAGE_RE.search(text or "")
    return f"Book {m.group(1)}, Page {m.group(2)}" if m else ""


def parse_specs(text):
    """Short human-readable property description from listing text."""
    parts = []
    t = PROPERTY_TYPE_RE.search(text or "")
    if t:
        parts.append(t.group(1).title().replace("Sgl Fam", "Single Family"))
    b = BEDS_RE.search(text or "")
    if b:
        parts.append(f"{b.group(1)} beds")
    ba = BATHS_RE.search(text or "")
    if ba:
        parts.append(f"{ba.group(1)} baths")
    return ", ".join(parts)


def parse_sewer(text):
    if SEPTIC_RE.search(text or ""):
        return "SEPTIC"
    if SEWER_RE.search(text or ""):
        return "SEWER"
    return ""


def split_address(addr):
    """'12 Main St Unit 3' -> ('12', 'MAIN ST', 'Unit 3')."""
    addr = clean(addr)
    m = re.match(r"^(\d+[A-Za-z]?)(?:\s*[-–&/]\s*\d+[A-Za-z]?)?\s+(.*)$", addr)
    if not m:
        return "", addr.upper(), ""
    rest = m.group(2)
    unit = ""
    um = re.search(r"[, ]+(?:Unit|Apt|Apartment|#|Suite|Ste)\.?\s*([\w-]+)$", rest, re.I)
    if um:
        unit = um.group(0).strip(" ,")
        rest = rest[: um.start()]
    return m.group(1), rest.upper().strip(" ,."), unit


ABBREV = {
    "STREET": "ST", "ROAD": "RD", "AVENUE": "AVE", "AV": "AVE", "DRIVE": "DR", "LANE": "LN",
    "CIRCLE": "CIR", "COURT": "CT", "PLACE": "PL", "TERRACE": "TER", "TERR": "TER",
    "BOULEVARD": "BLVD", "HIGHWAY": "HWY", "TURNPIKE": "TPKE", "TPK": "TPKE",
    "PARKWAY": "PKWY", "SQUARE": "SQ", "TRAIL": "TRL", "HEIGHTS": "HTS",
    "EXTENSION": "EXT", "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "MOUNT": "MT", "SAINT": "ST",
}


def normalize_street(street):
    words = re.sub(r"[^\w\s]", " ", street.upper()).split()
    return " ".join(ABBREV.get(w, w) for w in words)


def address_key(addr, town):
    num, street, unit = split_address(addr)
    base = f"{num} {normalize_street(street)} {unit.upper()} {(town or '').upper()}"
    return hashlib.sha1(" ".join(base.split()).encode()).hexdigest()[:12]


SEP = " ,|·-–\t\n:"
STATE_WORD = r"(MA|Mass\.?|Massachusetts|RI|NH|CT|ME|VT|NY)\b"
TOWN_AFTER_RE = re.compile(r"([A-Za-z][A-Za-z .'-]{1,30}?)\s*[,|·]?\s*" + STATE_WORD)
TOWN_BEFORE_RE = re.compile(r"([A-Za-z][A-Za-z .'-]{1,30}?)\s*[,|·]?\s*" + STATE_WORD + r"[\s,|·:-]*$")


def _town_near(after, before):
    """Town for a street address: 'Town, MA' after it, else 'Town, MA' before it.

    Returns (town, county, decided). decided=True means a town/state was found, even if it
    is outside our counties, so callers should not keep guessing.
    """
    a = after.lstrip(SEP)
    m = TOWN_AFTER_RE.match(a)
    if m:
        if not m.group(2).upper().startswith("MA"):
            return None, None, True
        town, county = resolve_town(m.group(1))
        return town, county, True
    m = TOWN_BEFORE_RE.search(before.rstrip(SEP) + " ")
    if not m:
        m = TOWN_BEFORE_RE.search(before)
    if m:
        if not m.group(2).upper().startswith("MA"):
            return None, None, True
        town, county = resolve_town(m.group(1).split("|")[-1])
        if town:
            return town, county, True
    # No state marker: accept a known town name that directly follows the street when a
    # Massachusetts ZIP (01xxx/02xxx) comes right after it.
    pm = PLACE_RE.match(a)
    if pm and re.match(r"[\s,|·-]*0[12]\d{3}\b", a[pm.end():]):
        town, county = resolve_town(pm.group(1))
        return town, county, bool(town)
    return None, None, False


def find_addresses(text):
    """All (start, end, street_address, town, county) found in text in one of our towns."""
    found = []
    for m in ADDRESS_RE.finditer(text or ""):
        addr = clean(m.group(1))
        if len(addr) > 70:
            continue
        after = text[m.end(): m.end() + 60]
        before = text[max(0, m.start() - 45): m.start()]
        town, county, _ = _town_near(after, before)
        if town:
            found.append((m.start(), m.end(), addr, town, county))
    return found


def unhyphen(text):
    """Re-join words that newspapers split across lines: 'Mas- sachusetts' -> 'Massachusetts'."""
    return re.sub(r"(?<=[a-z])- (?=[a-z])", "", text or "")

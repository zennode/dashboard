"""Assessor lookups against the local MassGIS index (see assessor_index.py).

Gives owner name + mailing address, year built, living area, building area, lot size,
style, rooms, assessed value and last sale for a street address, and finds the property
for a notice that names the borrower but not the address.
"""

import logging
import re

import assessor_index
import parse as P
from towns import TOWN_IDS

log = logging.getLogger(__name__)

DIRECTIONS = {"N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST"}
NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "ESQ"}
ORG_WORDS = re.compile(r"\b(LLC|INC|TRUST|TRUSTEE|CORP|CORPORATION|COMPANY|CO|LP|LLP|PROPERTIES|REALTY|HOLDINGS|ESTATE)\b", re.I)
USE_CODES = {
    "101": "Single Family", "102": "Condo", "103": "Mobile Home", "104": "Two Family",
    "105": "Three Family", "106": "Accessory Land", "109": "Multiple Houses", "111": "Apartments (4-8 units)",
    "112": "Apartments (9+ units)", "013": "Mixed Use", "031": "Mixed Use", "130": "Vacant Land",
    "131": "Vacant Land", "132": "Vacant Land",
}


class MassGIS:
    def __init__(self, folder):
        self.folder = folder
        self._towns = {}
        self._by_num = {}

    def _rows(self, town):
        tid = TOWN_IDS.get(town)
        if tid is None:
            return []
        if tid not in self._towns:
            rows = assessor_index.load_town(self.folder, tid)
            idx = {}
            for r in rows:
                r["_town"] = town
                num = str(r.get("ADDR_NUM") or "").split("-")[0].strip()
                if not num:
                    m = re.match(r"(\d+)", str(r.get("SITE_ADDR") or ""))
                    num = m.group(1) if m else ""
                if num:
                    idx.setdefault(num, []).append(r)
            self._towns[tid], self._by_num[tid] = rows, idx
        return self._towns[tid]

    # ------------------------------------------------------------- by address
    def by_address(self, address, town):
        num, street, unit = P.split_address(address)
        words = [w for w in P.normalize_street(street).split() if w not in DIRECTIONS] or street.split()
        if not num or not words or not self._rows(town):
            return None
        cands = [r for r in self._by_num[TOWN_IDS[town]].get(num, [])
                 if words[0] in P.normalize_street(str(r.get("FULL_STR") or r.get("SITE_ADDR") or "")).split()]
        if not cands:
            return None
        target = P.normalize_street(street)
        cands.sort(key=lambda r: (P.normalize_street(str(r.get("FULL_STR") or "")) != target,
                                  -(r.get("RES_AREA") or 0)))
        if unit:
            u = re.sub(r"\D", "", unit) or unit.split()[-1]
            unit_rows = [r for r in cands if u and u in f"{r.get('LOCATION', '')} {r.get('SITE_ADDR', '')}"]
            cands = unit_rows or cands
        return _summarize(cands[0])

    # ------------------------------------------------------------- by owner
    def by_owner(self, borrower, towns):
        """Find a borrower's property among `towns`. Returns a summary only when exactly
        one home matches, so a common name never gets pinned on the wrong house."""
        patterns = owner_patterns(borrower)
        if not patterns:
            return None
        hits = [r for t in towns for r in self._rows(t) if owner_matches(r.get("OWNER1"), patterns)]
        homes = [h for h in hits if (h.get("RES_AREA") or 0) > 0] or hits
        if len(homes) != 1:
            return None
        result = _summarize(homes[0])
        result["matched_by"] = "owner name"
        result["town"] = homes[0]["_town"]
        return result


    @staticmethod
    def summarize(row):
        return _summarize(row)

    def parcel(self, summary):
        """The assessor row behind a summary from by_owner/by_address (None if gone)."""
        pid, town = summary.get("parcel_id"), summary.get("town")
        if not pid or not town:
            return None
        return next((r for r in self._rows(town) if (r.get("PROP_ID") or r.get("LOC_ID")) == pid), None)

    def owner_candidates(self, borrower, towns):
        """Every parcel in `towns` whose owner shares a borrower's last name, for when the
        full name finds nothing or more than one home (trusts, other family members)."""
        lasts = last_names(borrower)
        if not lasts:
            return []
        return [r for t in towns for r in self._rows(t)
                if lasts & set(re.findall(r"[A-Z'-]+", str(r.get("OWNER1") or "").upper()))]


def _norm_owner(name):
    """'KABAMBA,CHRISTOPHORE M' and 'KABAMBA , CHRISTOPHORE' -> 'KABAMBA, CHRISTOPHORE M'"""
    return re.sub(r"\s*,\s*", ", ", re.sub(r"\s+", " ", str(name or "").upper())).strip()


def owner_matches(owner, patterns):
    owner = _norm_owner(owner)
    return bool(owner) and any(owner.startswith(p) for p in patterns)


def _people(borrower):
    """'Paul D. Mckunes and Janine E. Mckunes' -> [['PAUL', 'D', 'MCKUNES'], ['JANINE', 'E', 'MCKUNES']]"""
    out = []
    for person in re.split(r"\s+and\s+|,|;|&", borrower.strip()):
        parts = [re.sub(r"[^\w'-]", "", w).upper() for w in person.split()]
        parts = [p for p in parts if p and p not in NAME_SUFFIXES and len(p) > 1]
        if parts:
            out.append(parts)
    return out


def last_names(borrower):
    if not borrower or ORG_WORDS.search(borrower):
        return set()
    return {parts[-1] for parts in _people(borrower) if len(parts) >= 2}


def owner_patterns(borrower):
    """Assessor-style owner names to search for, for every borrower named.
    'Timothy J. Adams and Mary Adams' -> ['ADAMS TIMOTHY', 'ADAMS, TIMOTHY', 'ADAMS MARY', 'ADAMS, MARY']
    'Pablo Gonzalez Roman' -> ['ROMAN PABLO', 'ROMAN, PABLO', 'GONZALEZ ROMAN PABLO']
    'Prestigious One LLC' -> ['PRESTIGIOUS ONE']"""
    if not borrower:
        return []
    if ORG_WORDS.search(borrower):
        words = ORG_WORDS.split(borrower.upper())[0].split()
        name = " ".join(re.sub(r"[^\w]", "", w) for w in words[:3]).strip()
        return [name] if len(name) > 3 else []
    out = []
    for parts in _people(borrower):
        if len(parts) < 2:
            continue
        out += [f"{parts[-1]} {parts[0]}", f"{parts[-1]}, {parts[0]}"]
        if len(parts) >= 3:
            out.append(f"{parts[-2]} {parts[-1]} {parts[0]}")
    return list(dict.fromkeys(out))


def _summarize(r):
    lot = r.get("LOT_SIZE") or 0
    units = str(r.get("LOT_UNITS") or "").lower()
    if lot and (units.startswith("s") or "ft" in units or lot > 200):
        acres, sqft = lot / 43560, lot
    else:
        acres, sqft = lot, lot * 43560
    owner_mail = ", ".join(x for x in [
        str(r.get("OWN_ADDR") or "").strip(), str(r.get("OWN_CITY") or "").strip(),
        " ".join(x for x in [str(r.get("OWN_STATE") or "").strip(), str(r.get("OWN_ZIP") or "").strip()] if x),
    ] if x)
    ls_date = str(r.get("LS_DATE") or "")
    if re.fullmatch(r"\d{8}", ls_date):
        ls_date = f"{ls_date[4:6]}/{ls_date[6:]}/{ls_date[:4]}"
    code = str(r.get("USE_CODE") or "")[:3]
    owner_mail = P.clean(owner_mail)
    return {
        "parcel_id": r.get("PROP_ID") or r.get("LOC_ID") or "",
        "site_address": P.clean(str(r.get("SITE_ADDR") or "")).title(),
        "zip": r.get("ZIP") or "",
        "owner": P.clean(str(r.get("OWNER1") or "")),
        "owner_mailing": owner_mail.title(),
        "owner_occupied": _same_place(r),
        "year_built": r.get("YEAR_BUILT") or "",
        "living_area": r.get("RES_AREA") or "",
        "building_area": r.get("BLD_AREA") or "",
        "lot_acres": round(acres, 2) if lot else "",
        "lot_sqft": int(round(sqft)) if lot else "",
        "use": USE_CODES.get(code, ""),
        "style": str(r.get("STYLE") or "").strip().title(),
        "rooms": r.get("NUM_ROOMS") or "",
        "units": r.get("UNITS") or "",
        "assessed_value": r.get("TOTAL_VAL") or "",
        "assessed_fy": r.get("FY") or "",
        "last_sale_date": ls_date,
        "last_sale_price": r.get("LS_PRICE") or "",
        "matched_by": "address",
    }


def _same_place(r):
    if ORG_WORDS.search(str(r.get("OWNER1") or "")):
        return "No (company/trust)"
    site = P.normalize_street(str(r.get("SITE_ADDR") or ""))
    mail = P.normalize_street(str(r.get("OWN_ADDR") or ""))
    if not site or not mail:
        return ""
    return "Yes" if site.split()[:2] == mail.split()[:2] else "No"

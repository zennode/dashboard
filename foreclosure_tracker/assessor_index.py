"""Local copy of the MassGIS assessor tables for our towns.

MassGIS publishes every town's assessor table in one statewide zip (several GB, every
year since 2012). We read the zip's directory over HTTP range requests and pull out only
the latest table for each of our towns (~55 MB total), then save a compact copy. It is
rebuilt once a month; assessors update yearly.
"""

import gzip
import io
import json
import logging
import os
import re
import struct
import zipfile
from datetime import date

log = logging.getLogger(__name__)

PARCEL_PAGE = "https://www.mass.gov/info-details/massgis-data-property-tax-parcels"
FALLBACK_ZIP = ("https://s3.dualstack.us-east-1.amazonaws.com/download.massgis.digital.mass.gov/"
                "shapefiles/l3parcels/L3_AGGREGATE_SHP_20260702.zip")
KEEP = ["PROP_ID", "LOC_ID", "SITE_ADDR", "ADDR_NUM", "FULL_STR", "LOCATION", "CITY", "ZIP", "OWNER1",
        "OWN_ADDR", "OWN_CITY", "OWN_STATE", "OWN_ZIP", "YEAR_BUILT", "RES_AREA", "BLD_AREA", "LOT_SIZE",
        "LOT_UNITS", "USE_CODE", "STYLE", "NUM_ROOMS", "UNITS", "STORIES", "TOTAL_VAL", "FY", "LS_DATE",
        "LS_PRICE", "LS_BOOK", "LS_PAGE"]


class HttpRangeFile(io.RawIOBase):
    """Read-only, seekable file over HTTP range requests (enough for zipfile)."""

    def __init__(self, session, url, timeout=120):
        self.session, self.url, self.timeout, self.pos = session, url, timeout, 0
        r = session.head(url, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        self.size = int(r.headers["Content-Length"])

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def readinto(self, buf):
        if self.pos >= self.size:
            return 0
        end = min(self.size, self.pos + len(buf)) - 1
        for attempt in range(4):
            try:
                r = self.session.get(self.url, headers={"Range": f"bytes={self.pos}-{end}"}, timeout=self.timeout)
                r.raise_for_status()
                data = r.content
                break
            except Exception:
                if attempt == 3:
                    raise
        buf[:len(data)] = data
        self.pos += len(data)
        return len(data)


def read_dbf(raw, keep=KEEP):
    """Minimal dBASE III reader -> (header, rows) with only the `keep` columns."""
    nrec, hlen, rlen = struct.unpack("<IHH", raw[4:12])
    fields, off = [], 32
    while raw[off] != 0x0D:
        name = raw[off:off + 11].split(b"\x00")[0].decode("ascii", "ignore")
        ftype, flen = chr(raw[off + 11]), raw[off + 16]
        fields.append((name, ftype, flen))
        off += 32
    starts, pos = [], 1
    for name, ftype, flen in fields:
        starts.append(pos)
        pos += flen
    wanted = [(i, f) for i, f in enumerate(fields) if f[0] in keep]
    header = [f[0] for _, f in wanted]
    rows = []
    for n in range(nrec):
        rec = raw[hlen + n * rlen: hlen + (n + 1) * rlen]
        if not rec or rec[0:1] == b"*":
            continue
        row = []
        for i, (name, ftype, flen) in wanted:
            v = rec[starts[i]:starts[i] + flen].decode("utf-8", "replace").strip()
            if ftype in "NF" and v:
                try:
                    num = float(v)
                    v = int(num) if num.is_integer() else num
                except ValueError:
                    pass
            row.append(v)
        rows.append(row)
    return header, rows


def find_zip_url(http):
    try:
        html = http.get(PARCEL_PAGE).text
        m = re.findall(r"https://[^\"']+/shapefiles/l3parcels/L3_AGGREGATE_SHP_\d{8}\.zip", html)
        if m:
            return sorted(m)[-1]
    except Exception as e:
        log.warning("Could not read the MassGIS download page (%s); using the known file", e)
    return FALLBACK_ZIP


def build(http, folder, town_ids):
    """Download the latest assessor table for each MassGIS town id into `folder`."""
    os.makedirs(folder, exist_ok=True)
    url = find_zip_url(http)
    log.info("Reading assessor tables from %s", url)
    z = zipfile.ZipFile(io.BufferedReader(HttpRangeFile(http.session, url), buffer_size=1 << 20))
    latest = {}
    for info in z.infolist():
        m = re.match(r"L3_SHP_M(\d{3})_[^/]+/M\d{3}Assess_CY\d\d_FY(\d\d)\.dbf$", info.filename)
        if m:
            tid, fy = int(m.group(1)), int(m.group(2))
            if tid in town_ids and (tid not in latest or fy > latest[tid][0]):
                latest[tid] = (fy, info)
    for tid, (fy, info) in sorted(latest.items()):
        header, rows = read_dbf(z.read(info))
        with gzip.open(os.path.join(folder, f"M{tid:03d}.json.gz"), "wt", encoding="utf-8") as f:
            json.dump({"fy": 2000 + fy, "header": header, "rows": rows}, f)
        log.info("  town %03d FY%d: %d parcels", tid, 2000 + fy, len(rows))
    with open(os.path.join(folder, "manifest.json"), "w") as f:
        json.dump({"source": url, "built": date.today().isoformat(), "towns": sorted(latest)}, f)


def ensure(http, folder, town_ids, max_age_days=30):
    """Build or refresh the local index when it is missing, stale, or lacks a town."""
    path = os.path.join(folder, "manifest.json")
    try:
        with open(path) as f:
            man = json.load(f)
        age = (date.today() - date.fromisoformat(man["built"])).days
        if age <= max_age_days and set(town_ids) <= set(man["towns"]):
            return
    except (OSError, ValueError, KeyError):
        pass
    build(http, folder, set(town_ids))


def load_town(folder, tid):
    path = os.path.join(folder, f"M{tid:03d}.json.gz")
    if not os.path.exists(path):
        return []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    h = data["header"]
    return [dict(zip(h, r)) for r in data["rows"]]

"""Keep a Google Calendar in step with the tracker: one event per upcoming sale.

Only sales that are on as scheduled are on the calendar: "Scheduled" and "Notice only".
When a sale is postponed or cancelled its event is removed. Each event's id comes from
the sale's Key, so every run updates events in place instead of adding duplicates, and
events are only rewritten when something about the sale changed.

Setup: share the calendar with the service account ("Make changes to events"), enable the
Google Calendar API in its Cloud project, and put the calendar's ID in the
`calendar_id` in config.yaml (or the GOOGLE_CALENDAR_ID environment variable).
"""

import hashlib
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"
TZ = "America/New_York"
ON_CALENDAR = ("Scheduled", "Notice only")
TAG = {"auctionTracker": "1"}  # marks events this sync owns; nothing else is touched
SHEET_URL = "https://docs.google.com/spreadsheets/d/{id}/edit"


def _session():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/calendar.events"]
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        creds = Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(os.environ["GOOGLE_APPLICATION_CREDENTIALS"], scopes=scopes)
    return AuthorizedSession(creds)


def event_id(key):
    """Calendar ids allow 0-9 and a-v; hex digits fit."""
    return "auction" + hashlib.sha1(key.encode()).hexdigest()[:24]


def _sale_date(row):
    v = str(row.get("Auction Date") or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    return None


def _sale_time(row):
    v = str(row.get("Time") or "").strip().upper()
    for fmt in ("%I:%M %p", "%I:%M:%S %p", "%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(v, fmt).time()
        except ValueError:
            pass
    return None


def build_event(row, spreadsheet_id):
    d, t = _sale_date(row), _sale_time(row)
    addr, town = row.get("Address") or "", row.get("Town") or ""
    held_at = re.search(r"held at (.+?, MA)", str(row.get("Status Notes") or ""))
    if addr.lower().startswith("(municipal auction"):  # a town's multi-property sale
        title = f"Municipal auction: {addr.split(' - ', 1)[-1].rstrip(')')}"
    elif addr.startswith("("):  # "(borrower: ...)" / "(see legal notice)": no address yet
        title = f"Auction: address not found yet{', ' + town if town else ''} - see legal notice"
    else:
        title = f"Auction: {addr}, {town}" if town else f"Auction: {addr}"
    if row.get("Status") == "Notice only":
        title += " (legal notice only)"
    lines = [
        ("Auction company", row.get("Auction Company")),
        ("Deposit", row.get("Deposit")),
        ("Opening bid", row.get("Opening Bid")),
        ("Lender", row.get("Lender / Mortgagee")),
        ("Mortgage", " ".join(str(x) for x in (row.get("Mortgage Amount"), row.get("Mortgage Year")) if x)),
        ("Tax / liens", row.get("Tax / Water / Sewer Liens")),
        ("Septic / sewer", row.get("Septic / Sewer")),
        ("Property", " · ".join(str(x) for x in (
            row.get("Specs"), f"built {row['Year Built']}" if row.get("Year Built") else "",
            f"{row['Gross Living Area']} sqft" if row.get("Gross Living Area") else "",
            f"{row['Acres']} ac" if row.get("Acres") else "") if x)),
        ("Notes", row.get("Team Notes")),
        ("Status notes", row.get("Status Notes")),
        ("Listing", row.get("Listing Link")),
        ("Legal notice", row.get("Legal Notice")),
        ("Sheet", SHEET_URL.format(id=spreadsheet_id)),
    ]
    if not t:
        lines.insert(0, ("Time", "not posted yet - check the listing or legal notice"))
    body = {
        "summary": title,
        "location": (held_at.group(1) if held_at else
                     f"{addr}, {town}, MA" if town and not addr.startswith("(") else ""),
        "description": "\n".join(f"{k}: {v}" for k, v in lines if v),
        "status": "confirmed",
        "transparency": "transparent",  # don't block the day as busy
    }
    if t:
        start = datetime.combine(d, t)
        body["start"] = {"dateTime": start.isoformat(), "timeZone": TZ}
        body["end"] = {"dateTime": (start + timedelta(hours=1)).isoformat(), "timeZone": TZ}
    else:
        body["start"] = {"date": d.isoformat()}
        body["end"] = {"date": (d + timedelta(days=1)).isoformat()}
    digest = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    body["extendedProperties"] = {"private": {**TAG, "hash": digest}}
    return body


def wanted_events(rows, today, spreadsheet_id):
    out = {}
    for r in rows:
        d = _sale_date(r)
        if r.get("Status") in ON_CALENDAR and d and d >= today and r.get("Key"):
            out[event_id(r["Key"])] = build_event(r, spreadsheet_id)
    return out


def sync(rows, calendar_id, spreadsheet_id, today=None, session=None):
    """Create, update and remove events. Returns a short summary for Source Health."""
    today = today or date.today()
    s = session or _session()
    base = API.format(cal=quote(calendar_id, safe=""))
    want = wanted_events(rows, today, spreadsheet_id)

    # Events this sync made from yesterday on (past sales stay as a record).
    have, page = {}, None
    while True:
        params = {"privateExtendedProperty": "auctionTracker=1", "showDeleted": "true", "maxResults": 2500,
                  "timeMin": f"{today - timedelta(days=1)}T00:00:00Z", "singleEvents": "true"}
        if page:
            params["pageToken"] = page
        r = s.get(base, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        for ev in data.get("items", []):
            have[ev["id"]] = ev
        page = data.get("nextPageToken")
        if not page:
            break

    added = updated = removed = 0
    for eid, body in want.items():
        ev = have.get(eid)
        if ev and ev.get("status") != "cancelled" and \
                ev.get("extendedProperties", {}).get("private", {}).get("hash") == body["extendedProperties"]["private"]["hash"]:
            continue
        if ev:  # changed, or deleted earlier (a postponed sale back on): update brings it back
            r = s.put(f"{base}/{eid}", json=body, timeout=60)
            updated += 1
        else:
            r = s.post(base, json={**body, "id": eid}, timeout=60)
            if r.status_code == 409:  # deleted long ago and no longer listed: restore it
                r = s.put(f"{base}/{eid}", json=body, timeout=60)
            added += 1
        r.raise_for_status()
    for eid, ev in have.items():
        start = (ev.get("start") or {}).get("date") or (ev.get("start") or {}).get("dateTime", "")[:10]
        if eid not in want and ev.get("status") != "cancelled" and start >= today.isoformat():
            r = s.delete(f"{base}/{eid}", timeout=60)
            if r.status_code not in (200, 204, 404, 410):
                r.raise_for_status()
            removed += 1
    log.info("calendar: %d on calendar, %d added, %d updated, %d removed", len(want), added, updated, removed)
    return {"events": len(want), "added": added, "updated": updated, "removed": removed}


def main():
    """Sync the calendar by hand, outside a tracker run: read the tracker tabs as they
    stand (including anything typed in since the last run) and bring the calendar in line."""
    import argparse

    import yaml

    from sheets import SheetWriter

    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    cal = os.environ.get("GOOGLE_CALENDAR_ID") or cfg.get("calendar_id")
    if not cal:
        raise SystemExit("No calendar_id in config.yaml")
    sheet = SheetWriter(cfg["spreadsheet_id"])
    rows = sheet.read_rows(cfg["tabs"]["tracker"]) + sheet.read_rows(cfg["tabs"]["cancelled"])
    sync(rows, cal, cfg["spreadsheet_id"])


if __name__ == "__main__":
    main()

"""Build the Friday email: next week's auctions from the tracker sheet.

Usage:
  python weekly_email.py --xlsx sheet.xlsx --out email.html [--week-of 2026-10-26]

Reads "Auto - Auction Tracker", "Auto - Cancelled & Postponed" and "Auto - Status
Changes" from an .xlsx download of the sheet. Writes the HTML email to --out and prints a
JSON summary (subject, counts, the sentinel title) for the send step's checks.
"""

import argparse
import html
import json
from datetime import date, datetime, timedelta

import openpyxl
from dateutil import parser as dateparser

TITLE = "Foreclosure Auctions Next Week"
SHEET_URL = "https://docs.google.com/spreadsheets/d/1jURtqtgCVPJKUb1nPgAya-gLPw86Tzw9OeCBxhlvZhk/edit"
SHOW = ("Scheduled", "Postponed", "Notice only")

NAVY, BLUE, LIGHT, PALE, ICE = "#1e3a5f", "#2d5a8e", "#93c5fd", "#bfdbfe", "#eff6ff"
RED, GREEN, AMBER, GRAY, SLATE, BORDER = "#dc2626", "#059669", "#b45309", "#6b7280", "#f8fafc", "#e5e7eb"


def read_tab(wb, name):
    if name not in wb.sheetnames:
        return []
    rows = [r for r in wb[name].iter_rows(values_only=True) if any(c not in (None, "") for c in r)]
    if not rows:
        return []
    header = [str(h or "") for h in rows[0]]
    return [dict(zip(header, r)) for r in rows[1:]]


def as_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except ValueError:
            pass
    return None


def as_datetime(v):
    if isinstance(v, datetime):
        return v
    try:
        return dateparser.parse(str(v)) if v not in (None, "") else None
    except (ValueError, OverflowError):
        return None


def as_time(v):
    if v in (None, ""):
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%-I:%M %p")
    return str(v).strip()


def money(v):
    if v in (None, ""):
        return ""
    if isinstance(v, (int, float)):
        return f"${v:,.0f}"
    return str(v)


def num(v, suffix=""):
    if v in (None, ""):
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (int, float)):
        return f"{v:,}{suffix}"
    return f"{v}{suffix}"


def minutes(t):
    try:
        return datetime.strptime(t, "%I:%M %p").hour * 60 + datetime.strptime(t, "%I:%M %p").minute
    except ValueError:
        return 9999


def next_week(today):
    """The Monday-Sunday after `today` (run on Friday -> the coming week)."""
    monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    return monday, monday + timedelta(days=6)


def e(v):
    return html.escape(str(v if v is not None else ""))


def card(r):
    status = r.get("Status") or ""
    color = {"Postponed": AMBER, "Notice only": BLUE}.get(status, GREEN)
    addr = f"{e(r.get('Address'))}, {e(r.get('Town'))}"
    when = " &nbsp;|&nbsp; ".join(e(x) for x in (r.get("_time"), r.get("Auction Company")) if x)
    line2 = " &nbsp;|&nbsp; ".join(x for x in [
        f"Deposit: {e(money(r.get('Deposit')))}" if r.get("Deposit") else "",
        f"Lender: {e(r.get('Lender / Mortgagee'))}" if r.get("Lender / Mortgagee") else "",
        f"Mortgage: {e(money(r.get('Mortgage Amount')))}" if r.get("Mortgage Amount") else "",
        f"Yr {e(num(r.get('Mortgage Year')).replace(',', ''))}" if r.get("Mortgage Year") else "",
        e(r.get("1st or 2nd")) if r.get("1st or 2nd") else "",
    ] if x)
    line3 = " &nbsp;|&nbsp; ".join(x for x in [
        e(r.get("Specs")) if r.get("Specs") else "",
        f"Built {e(num(r.get('Year Built')).replace(',', ''))}" if r.get("Year Built") else "",
        f"{e(num(r.get('Gross Living Area')))} sqft" if r.get("Gross Living Area") else "",
        f"{e(num(r.get('Acres')))} ac" if r.get("Acres") else "",
        e(r.get("Septic / Sewer")) if r.get("Septic / Sewer") else "",
    ] if x)
    notes = []
    if status == "Postponed":
        notes.append(f"Postponed{(' from ' + e(r['_orig'])) if r.get('_orig') else ''}")
    if status == "Notice only":
        notes.append("Only in the legal notice - not on any auctioneer website")
    if r.get("Owner Occupied") == "Yes":
        notes.append("Owner-occupied")
    if r.get("Assessed Value"):
        notes.append(f"Assessed {e(money(r['Assessed Value']))}")
    link = r.get("Listing Link") or (str(r.get("Legal Notice") or "").split() or [""])[0]
    link_html = (f' &nbsp;<a href="{e(link)}" style="color:{BLUE};text-decoration:underline;">'
                 f'{"Listing" if r.get("Listing Link") else "Notice"}</a>') if link else ""
    return (
        f'<div style="background-color:{SLATE};border-left:4px solid {color};padding:12px 16px;margin-bottom:10px;'
        f'border-radius:0 6px 6px 0;">'
        f'<p style="margin:0 0 4px;font-size:14px;font-weight:700;color:{NAVY};">{addr}</p>'
        f'<p style="margin:0;font-size:13px;color:#374151;">{when}</p>'
        + (f'<p style="margin:4px 0 0;font-size:13px;color:#374151;">{line2}</p>' if line2 else "")
        + (f'<p style="margin:4px 0 0;font-size:13px;color:{GRAY};">{line3}</p>' if line3 else "")
        + (f'<p style="margin:4px 0 0;font-size:13px;color:{color};font-weight:600;">'
           f'{" &nbsp;|&nbsp; ".join(notes)}{link_html}</p>' if notes or link_html else "")
        + "</div>"
    )


def build(wb, today):
    start, end = next_week(today)
    rows = read_tab(wb, "Auto - Auction Tracker") + read_tab(wb, "Auto - Cancelled & Postponed")
    week, seen = [], set()
    for r in rows:
        d = as_date(r.get("Auction Date"))
        if not d or not (start <= d <= end) or r.get("Status") not in SHOW:
            continue
        key = r.get("Key") or f"{r.get('Address')}|{r.get('Town')}"
        if key in seen:
            continue
        seen.add(key)
        r["_date"], r["_time"] = d, as_time(r.get("Time"))
        od = as_date(r.get("Original Date"))
        r["_orig"] = f"{od:%m/%d}" if od else ""
        week.append(r)
    week.sort(key=lambda r: (r["_date"], minutes(r["_time"]), str(r.get("Town"))))

    # Changes logged in the last 7 days, for sales in or after next week.
    changes = []
    for c in read_tab(wb, "Auto - Status Changes"):
        when = as_datetime(c.get("Detected (ET)"))
        kind = str(c.get("Change") or "")
        sale = as_date(c.get("Auction Date"))
        # Skip changes on past sales and on far-future dates (misread years).
        if not sale or not (today <= sale <= today + timedelta(days=125)):
            continue
        if when and when.date() > today - timedelta(days=7) and (
                "Postponed" in kind or "Cancelled" in kind or "Removed" in kind):
            changes.append(c)

    counts = {s: sum(1 for r in week if r["Status"] == s) for s in SHOW}
    towns = sorted({str(r.get("Town")) for r in week if r.get("Town")})
    headline_color = GREEN if week else AMBER
    headline = (f"{len(week)} auction{'s' if len(week) != 1 else ''} {start:%b %-d} - {end:%b %-d}"
                + (f" across {len(towns)} town{'s' if len(towns) != 1 else ''}" if week else ""))

    parts = [
        '<div style="max-width:750px;margin:0 auto;font-family:Arial,Helvetica,sans-serif;background-color:#ffffff;">',
        f'<div style="background-color:#1e3a5f;background-image:linear-gradient(135deg,#1e3a5f,#2d5a8e);padding:24px 30px;border-radius:8px 8px 0 0;">'
        f'<h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:700;">{TITLE}</h1>'
        f'<p style="margin:6px 0 0;color:{LIGHT};font-size:14px;">Berkshire, Hampden, Hampshire, Franklin &amp; '
        f'Worcester — week of {start:%b %-d, %Y}</p></div>',
        f'<div style="background-color:{headline_color};padding:14px 30px;text-align:center;">'
        f'<span style="color:#ffffff;font-size:16px;font-weight:700;">{e(headline)}</span></div>',
        f'<div style="padding:16px 30px;background-color:{ICE};border-bottom:1px solid {PALE};">'
        f'<span style="font-size:14px;color:{NAVY};"><strong>Scheduled:</strong> {counts["Scheduled"]} &nbsp;|&nbsp; '
        f'<strong>Postponed to this week:</strong> {counts["Postponed"]} &nbsp;|&nbsp; '
        f'<strong>Legal notice only:</strong> {counts["Notice only"]}</span></div>',
    ]

    # Day-by-day summary table.
    parts.append(f'<div style="padding:12px 30px;background-color:{SLATE};border-bottom:2px solid {NAVY};">'
                 f'<h2 style="margin:0;color:{NAVY};font-size:16px;font-weight:700;">Schedule</h2></div>')
    th = (f'style="padding:10px 14px;text-align:left;color:#ffffff;font-size:13px;font-weight:600;'
          f'border-bottom:2px solid {LIGHT};"')
    parts.append(f'<table style="width:100%;border-collapse:collapse;"><thead><tr style="background-color:{NAVY};">'
                 f'<th {th}>Day</th><th {th}>Time</th><th {th}>Address</th><th {th}>Deposit</th></tr></thead><tbody>')
    if not week:
        parts.append(f'<tr style="background-color:#f9fafb;"><td colspan="4" style="padding:10px 14px;font-size:14px;'
                     f'border-bottom:1px solid {BORDER};">No auctions found for next week.</td></tr>')
    for i, r in enumerate(week):
        bg = "#f9fafb" if i % 2 == 0 else "#ffffff"
        td = f'style="padding:10px 14px;font-size:14px;border-bottom:1px solid {BORDER};"'
        parts.append(f'<tr style="background-color:{bg};"><td {td}>{r["_date"]:%a %-m/%-d}</td><td {td}>{e(r["_time"])}</td>'
                     f'<td {td}>{e(r.get("Address"))}, {e(r.get("Town"))}</td><td {td}>{e(money(r.get("Deposit")))}</td></tr>')
    parts.append("</tbody></table>")

    # Detail cards grouped by day.
    day = None
    for r in week:
        if r["_date"] != day:
            if day is not None:
                parts.append("</div>")
            day = r["_date"]
            parts.append(f'<div style="padding:12px 30px;background-color:{SLATE};border-bottom:2px solid {NAVY};">'
                         f'<h2 style="margin:0;color:{NAVY};font-size:16px;font-weight:700;">{day:%A, %B %-d}</h2></div>'
                         f'<div style="padding:12px 30px;">')
        parts.append(card(r))
    if day is not None:
        parts.append("</div>")

    if changes:
        parts.append(f'<div style="padding:12px 30px;background-color:{SLATE};border-bottom:2px solid {NAVY};">'
                     f'<h2 style="margin:0;color:{NAVY};font-size:16px;font-weight:700;">Changes This Past Week</h2></div>'
                     f'<div style="padding:16px 30px;">')
        for c in changes[-15:]:
            kind = str(c.get("Change"))
            color = RED if ("Cancelled" in kind or "Removed" in kind) else AMBER
            detail = " → ".join(str(x) for x in (c.get("Previous"), c.get("Now")) if x not in (None, ""))
            parts.append(f'<p style="margin:0 0 6px;font-size:13px;color:#374151;"><strong style="color:{color};">'
                         f'{e(kind)}</strong> — {e(c.get("Address"))}, {e(c.get("Town"))}'
                         f'{(" (" + e(detail) + ")") if detail else ""}</p>')
        parts.append("</div>")

    parts.append(f'<div style="padding:16px 30px;background-color:{SLATE};"><h3 style="margin:0 0 8px;color:{NAVY};'
                 f'font-size:14px;font-weight:700;">Links</h3><p style="margin:0 0 4px;font-size:13px;">'
                 f'<a href="{SHEET_URL}" style="color:{BLUE};text-decoration:underline;">MA Weekly Auctions sheet</a></p>'
                 f'<p style="margin:0;font-size:13px;color:{GRAY};">Times and deposits are as listed by the auctioneer '
                 f'or legal notice; confirm with the auctioneer before attending.</p></div>')
    parts.append(f'<div style="padding:16px 30px;background-color:#f3f4f6;border-top:2px solid {NAVY};text-align:center;">'
                 f'<p style="margin:0;font-size:12px;color:{GRAY};">Auto-generated from Yellow Brick Properties</p></div>')
    parts.append("</div>")
    body = "".join(parts)

    subject = f"Foreclosure Auctions: Week of {start:%b %-d, %Y} — {len(week)} auction{'s' if len(week) != 1 else ''}"
    summary = {"subject": subject, "sentinel": TITLE, "week_start": start.isoformat(), "week_end": end.isoformat(),
               "auctions": len(week), "counts": counts, "changes": len(changes),
               "min_rows": len(week) + 1, "html_chars": len(body)}
    return body, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True, help="the tracker sheet downloaded as .xlsx")
    ap.add_argument("--out", required=True, help="where to write the HTML email")
    ap.add_argument("--week-of", help="pretend today is the Friday before this Monday (YYYY-MM-DD)")
    args = ap.parse_args()
    today = date.today()
    if args.week_of:
        today = date.fromisoformat(args.week_of) - timedelta(days=3)
    wb = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)
    body, summary = build(wb, today)
    with open(args.out, "w") as f:
        f.write(body)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()

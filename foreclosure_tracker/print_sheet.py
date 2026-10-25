"""A printable clipboard sheet of next week's auctions (landscape letter PDF).

One row per sale, grouped by day: a checkbox, time, property, auctioneer, deposit, tax /
lien amounts, septic / sewer and the sheet's Team Notes, with room to write a max bid.

  python print_sheet.py --xlsx sheet.xlsx --out auctions.pdf [--week-of 2026-10-26]
"""

import argparse
from datetime import date, timedelta
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from weekly_email import money, num, select_week

NAVY = colors.HexColor("#1e3a5f")
PALE = colors.HexColor("#e8eef6")
GRID = colors.HexColor("#9ca3af")
AMBER = colors.HexColor("#b45309")
BLUE = colors.HexColor("#2d5a8e")

BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=9, leading=11)
SMALL = ParagraphStyle("small", parent=BASE, fontSize=7.5, leading=9, textColor=colors.HexColor("#4b5563"))
BOLD = ParagraphStyle("bold", parent=BASE, fontName="Helvetica-Bold", fontSize=10, leading=12)
HEAD = ParagraphStyle("head", parent=BASE, fontName="Helvetica-Bold", textColor=colors.white)
DAY = ParagraphStyle("day", parent=BASE, fontName="Helvetica-Bold", fontSize=11, leading=13, textColor=NAVY)
TITLE = ParagraphStyle("title", parent=BASE, fontName="Helvetica-Bold", fontSize=16, leading=19, textColor=NAVY)

# Checkbox, time, property, auctioneer, deposit, tax/liens, septic, notes (10" usable width).
WIDTHS = [0.3 * inch, 0.82 * inch, 2.38 * inch, 1.05 * inch, 0.75 * inch, 1.2 * inch, 1.1 * inch,
          2.4 * inch]
HEADER = ["", "Time", "Property", "Auctioneer", "Deposit", "Tax / Liens", "Septic / Sewer",
          "Notes / max bid"]
SEPTIC_GUESSES = {"Likely SEPTIC - verify", "Verify w/ BOH"}
DAY_BG = colors.HexColor("#c7d6ea")


class Box(Flowable):
    """An empty checkbox to tick off a sale."""

    def wrap(self, *args):
        return 11, 11

    def draw(self):
        self.canv.setLineWidth(1)
        self.canv.rect(0, 0, 11, 11)


def _p(text, style=BASE):
    return Paragraph(escape(str(text or "")), style)


def _short(v, n):
    v = str(v or "")
    return v if len(v) <= n else v[:n - 1].rstrip() + "…"


def _row(r):
    status = r.get("Status") or ""
    town = r.get("Town") or ""
    specs = " · ".join(x for x in [
        _short(r.get("Specs"), 45),
        f"Built {num(r.get('Year Built')).replace(',', '')}" if r.get("Year Built") else "",
        f"{num(r.get('Gross Living Area'))} sqft" if r.get("Gross Living Area") else "",
        f"{num(r.get('Acres'))} ac" if r.get("Acres") else "",
    ] if x)
    prop = [_p(r.get("Address"), BOLD), _p(town)]
    if specs:
        prop.append(_p(specs, SMALL))

    flags = []
    if status == "Postponed":
        flags.append(f"POSTPONED{(' from ' + r['_orig']) if r.get('_orig') else ''}")
    if status == "Notice only":
        flags.append("LEGAL NOTICE ONLY")
    if r.get("Owner Occupied") == "Yes":
        flags.append("Owner-occupied")
    if r.get("Assessed Value"):
        flags.append(f"Assessed {money(r['Assessed Value'])}")
    if r.get("Lender / Mortgagee"):
        flags.append(f"Lender: {_short(r['Lender / Mortgagee'], 40)}")
    if r.get("Mortgage Amount"):
        flags.append(f"Mtg {money(r['Mortgage Amount'])}")
    # Team Notes (e.g. from the VA call) first and prominent, then the tracker's facts.
    notes = [_p(r["Team Notes"], BASE)] if r.get("Team Notes") else []
    if flags:
        notes.append(_p(" · ".join(flags), ParagraphStyle(
            "flag", parent=SMALL, textColor=AMBER if status == "Postponed" else BLUE)))

    septic = str(r.get("Septic / Sewer") or "")
    septic_cell = _p(septic, SMALL) if septic in SEPTIC_GUESSES else _p(septic)
    return [Box(), _p(r.get("_time") or "TBD", BOLD), prop, _p(_short(r.get("Auction Company"), 22)),
            _p(money(r.get("Deposit")) or "?", BOLD), _p(r.get("Tax / Water / Sewer Liens")),
            septic_cell, notes]


def build_pdf(week, start, end, printed=None):
    printed = printed or date.today()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter), leftMargin=0.5 * inch,
                            rightMargin=0.5 * inch, topMargin=0.5 * inch, bottomMargin=0.55 * inch,
                            title=f"Foreclosure auctions {start:%b %-d} - {end:%b %-d, %Y}")

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#6b7280"))
        canvas.drawString(0.5 * inch, 0.32 * inch,
                          f"Printed {printed:%a %b %-d, %Y} from the MA Weekly Auctions sheet. "
                          "Confirm time, place and deposit with the auctioneer before going.")
        canvas.drawRightString(10.5 * inch, 0.32 * inch, f"Page {d.page}")
        canvas.restoreState()

    story = [
        _p(f"Foreclosure Auctions: {start:%b %-d} – {end:%b %-d, %Y}", TITLE),
        _p(f"{len(week)} auction{'s' if len(week) != 1 else ''} · Berkshire, Hampden, Hampshire, "
           "Franklin & Worcester", SMALL),
        Spacer(1, 8),
    ]
    if not week:
        story.append(_p("No foreclosure auctions found for this week.", BOLD))

    # One table for the whole week so pages fill up; each day gets a heading row.
    data, style = [[_p(h, HEAD) for h in HEADER]], [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]
    day = start
    while day <= end:
        rows = [r for r in week if r["_date"] == day]
        if rows:
            i = len(data)
            data.append([_p(f"{day:%A, %B %-d}  ({len(rows)})", DAY)] + [""] * (len(HEADER) - 1))
            style += [("SPAN", (0, i), (-1, i)), ("BACKGROUND", (0, i), (-1, i), DAY_BG)]
            for n, r in enumerate(rows):
                data.append(_row(r))
                style.append(("BOTTOMPADDING", (0, len(data) - 1), (-1, len(data) - 1), 12))
                if n % 2:
                    style.append(("BACKGROUND", (0, len(data) - 1), (-1, len(data) - 1), PALE))
        day += timedelta(days=1)
    if week:
        story.append(Table(data, colWidths=WIDTHS, repeatRows=1, style=TableStyle(style)))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def main():
    import openpyxl

    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--week-of", help="Monday of the week (YYYY-MM-DD)")
    args = ap.parse_args()
    today = date.fromisoformat(args.week_of) - timedelta(days=3) if args.week_of else date.today()
    wb = openpyxl.load_workbook(args.xlsx, read_only=True, data_only=True)
    start, end, week = select_week(wb, today)
    with open(args.out, "wb") as f:
        f.write(build_pdf(week, start, end, printed=today))


if __name__ == "__main__":
    main()

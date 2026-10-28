"""Opening / starting bids posted on Xome.

Xome markets foreclosure sales run by other auctioneers, so it only adds a bid to sales
the tracker already found; it never adds a sale or names Xome as the auction company.
The state results pages (allowed by Xome's robots.txt; its /api/ is not) carry every
listing as JSON inside the HTML.
"""

import logging
import re
from datetime import date, timedelta

import parse as P
from towns import resolve_town

log = logging.getLogger(__name__)

URL = "https://www.xome.com/auctions/ma"
BID_TYPES = ("opening bid", "starting bid")  # not "Final Bid", which is a past result


def _field(chunk, name):
    m = re.search(r'"%s":(null|"((?:[^"\\]|\\.)*)"|[-\d.]+|true|false)' % re.escape(name), chunk)
    if not m or m.group(1) == "null":
        return ""
    return m.group(2) if m.group(2) is not None else m.group(1)


def parse_page(html, today):
    """[(address, town, bid text, sale date)] for upcoming sales with a bid posted."""
    text = html.replace('\\"', '"').replace("\\u0026", "&")
    out = []
    for chunk in text.split('{"listingID":')[1:]:
        bid_type = _field(chunk, "bidType").strip()
        amount = P.parse_money(_field(chunk, "bidAmount").replace("$$", "$"))
        sale = _field(chunk, "auctionStartDate")[:10]
        if not amount or not any(t in bid_type.lower() for t in BID_TYPES):
            continue
        if not sale or sale < (today - timedelta(days=1)).isoformat():
            continue
        town, _ = resolve_town(_field(chunk, "city"))
        if not town:
            continue
        bid = P.fmt_money(amount) + (" est." if "est" in bid_type.lower() else "") + " (Xome)"
        out.append((P.clean(_field(chunk, "streetAddress")), town, bid, sale))
    return out


def total_pages(html):
    m = re.search(r'\\?"totalPages\\?":(\d+)', html)
    return int(m.group(1)) if m else 1


def bids(http, today=None):
    """{address_key: bid text} for Massachusetts sales with a posted opening/starting bid."""
    today = today or date.today()
    first = http.get(URL).text
    found = parse_page(first, today)
    for page in range(2, min(total_pages(first), 10) + 1):
        found += parse_page(http.get(URL, params={"page": page}).text, today)
    return {P.address_key(addr, town): bid for addr, town, bid, _ in found}

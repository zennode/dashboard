"""Parsing checks built from real listing and notice text. Run: python -m pytest -q"""

from datetime import date

import parse as P
from massgis import owner_patterns
from sources.auction_sites import build_record
from sources.public_notices import parse_notice

TODAY = date(2026, 10, 23)


def test_addresses_pick_the_right_town():
    text = ("Springfield, MA | 85 Penncastle Street | Single Family Home | Mon. Oct. 26, 2026 at 11 am | on | "
            "Natick, MA | 137 Howe Street | 2 Family")
    assert [(a, t) for _, _, a, t, _ in P.find_addresses(text)] == [("85 Penncastle Street", "Springfield")]
    assert P.find_addresses("#2666 | Lynn, MA | - 5 River Street Ct") == []
    hit = P.find_addresses("92 Old East Brookfield Road NORTH BROOKFIELD MA")[0]
    assert (hit[2], hit[3], hit[4]) == ("92 Old East Brookfield Road", "North Brookfield", "Worcester")
    assert P.find_addresses("13 Pineywood Road | Southwick | MA | 01077")[0][3] == "Southwick"
    assert P.find_addresses("32345 NORTHAMPTON DR | Warren | MI")[:1] == []


def test_dates_times_and_money():
    assert P.parse_date("Tuesday Oct 27 @ 11:00 am", TODAY) == date(2026, 10, 27)
    assert P.parse_date("Jan 12th", TODAY) == date(2027, 1, 12)  # no year, already past -> next year
    assert P.parse_time("10/27/2026 @ 11:00:00 AM") == "11:00 AM"
    assert P.parse_time("@ 12pm (Noon)") == "12:00 PM"
    assert P.parse_deposit("Required Deposit : $10,000") == "$10,000"


def test_postponement_is_detected():
    rec = build_record("Baystate", "", "9 Denver Street", "Springfield", "Hampden",
                       "Friday, November 13, 2026 @ 2:00 pm (Postponed to 12-14-2026 @ 12:00 PM)", TODAY)
    assert rec["status"] == "Postponed"
    assert rec["auction_date"] == "2026-12-14" and rec["original_date"] == "2026-11-13"
    rec = build_record("Skypoint", "", "158 Morningside Rd", "Worcester", "Worcester",
                       "AUCTION: | Tuesday, January 12th @ 1:00 PM | Previously scheduled for | Wednesday, October 14th @ 1:00 PM",
                       TODAY)
    assert rec["status"] == "Postponed" and rec["auction_date"] == "2027-01-12"


def test_notice_snippet():
    row = {"notice_id": "1", "newspaper": "Republican, The", "published": "Tuesday, October 20, 2026",
           "paper_location": "City: Springfield County: Hampden",
           "snippet": ("LEGAL NOTICE MORTGAGEE 'S SALE OF REAL ESTATE By virtue of and in execution of the Power of "
                       "Sale con- tained in a certain mortgage given by Wilfredo J. Semprit to RBS Citizens, N.A., "
                       "dated February 26, 2015 and recorded in Hampden County Registry of Deeds in Book 20608, "
                       "Page 58 (the Mortgage)")}
    n = parse_notice(row, TODAY)
    assert n["county"] == "Hampden" and n["borrower"] == "Wilfredo J. Semprit"
    assert n["lender"] == "RBS Citizens, N.A." and n["mortgage_date"] == "2015-02-26"
    assert n["book_page"] == "Book 20608, Page 58"
    row["snippet"] = row["snippet"].replace("Hampden County", "Suffolk County")
    assert parse_notice(row, TODAY) is None  # recorded outside our counties


def test_owner_name_patterns():
    assert owner_patterns("Pablo Gonzalez Roman") == ["ROMAN PABLO", "ROMAN, PABLO", "GONZALEZ ROMAN PABLO"]
    assert owner_patterns("Prestigious One LLC") == ["PRESTIGIOUS ONE"]
    assert owner_patterns("Timothy J. Adams and Mary Adams")[0] == "ADAMS TIMOTHY"


def test_deposit_wording():
    assert P.parse_deposit("Terms of Sale: $25,000.00 Initial Deposit Cash or Certified Funds") == "$25,000"
    assert P.parse_deposit("shall deposit a bank treasurer's check, or certified check in the amount of "
                           "Two Hundred Fifty Thousand Dollars ($250,000.00) at the time") == "$250,000"
    assert P.parse_deposit("Terms of Sale $5,000 deposit by bank check.") == "$5,000"
    assert P.parse_deposit("Opening bid $300,000. Deposit to be increased to 10%") == ""


def test_stale_dates_without_a_year_stay_in_the_past():
    assert P.parse_date("MARCH 25TH", TODAY) == date(2026, 3, 25)


def test_notice_lookup_helpers():
    from sources.public_notices import _ordinal
    from tracker import _town_from_notice
    assert [_ordinal(n) for n in (1, 2, 3, 11, 22, 31)] == ["1st", "2nd", "3rd", "11th", "22nd", "31st"]
    assert _town_from_notice(["Grafton", "Worcester"], "Worcester") == "Grafton"
    assert _town_from_notice(["Worcester"], "Worcester") == "Worcester"
    assert _town_from_notice(["Charlton", "Dudley", "Worcester"], "Worcester") == ""


def test_propstream_export():
    import propstream
    export = [
        {"Address": "14 Talcott Ave", "City": "Springfield", "Est. Equity": "$142,000",
         "Mortgage 1 Amount": "$98,500", "Mortgage 1 Recording Date": "01/22/2009", "Mortgage 1 Lender": "James B Nutter"},
        {"Address": "13 Pineywood Rd", "City": "Southwick", "Mortgage 1 Amount": "150000",
         "Mortgage 1 Recording Date": "2001-05-01", "Mortgage 1 Lender": "Big Bank", "Mortgage 2 Amount": "60,000",
         "Mortgage 2 Recording Date": "2008-03-02", "Mortgage 2 Lender": "M&T Bank"},
    ]
    rows = [{"Key": "a", "Address": "14 Talcott Street", "Town": "Springfield", "Mortgage Year": "2009",
             "1st or 2nd": "1st (presumed)", "Status": "Scheduled"},
            {"Key": "b", "Address": "13 Pineywood Road", "Town": "Southwick", "Mortgage Year": "2008",
             "Status": "Scheduled"}]
    a, b = propstream.apply(rows, export)
    assert (a["Mortgage Amount"], a["1st or 2nd"], a["Est. Equity"]) == ("$98,500", "1st", "$142,000")
    # The notice's 2008 mortgage is PropStream's second loan.
    assert (b["Mortgage Amount"], b["Lender / Mortgagee"], b["1st or 2nd"]) == ("$60,000", "M&T Bank", "2nd")
    assert propstream.lookup_rows(rows) == []


def test_propstream_phones():
    import propstream
    export = [{"Address": "14 Talcott Ave", "City": "Springfield", "Phone 1": "(413) 555-0100", "Phone 1 Type": "Mobile",
               "Phone 1 DNC": "No", "Phone 2": "1-413-555-0199", "Phone 2 Type": "Landline", "Phone 2 DNC": "Yes"}]
    calls = [{"Key": "a", "Property Address": "14 Talcott Street", "Town": "Springfield", "Phone": ""},
             {"Key": "b", "Property Address": "14 Talcott Street", "Town": "Springfield", "Phone": "typed"}]
    a, b = propstream.apply_phones(calls, export)
    assert a["Phone"] == "413-555-0100 (Mobile); 413-555-0199 (Landline, DNC)"
    assert b["Phone"] == "typed"


def test_typed_septic_kept():
    from tracker import keep_sheet_edits, TRACKER_KEEP_UNLESS
    rows = [{"Key": "a", "Septic / Sewer": "Verify w/ BOH"}, {"Key": "b", "Septic / Sewer": "SEWER"},
            {"Key": "c", "Septic / Sewer": "Likely SEPTIC - verify"}]
    typed = [{"Key": "a", "Septic / Sewer": "Septic, Title 5 passed 2023"},
             {"Key": "b", "Septic / Sewer": "Verify w/ BOH"}, {"Key": "c", "Septic / Sewer": ""}]
    a, b, c = keep_sheet_edits(rows, typed, set(), set(), TRACKER_KEEP_UNLESS)
    assert a["Septic / Sewer"] == "Septic, Title 5 passed 2023"
    assert b["Septic / Sewer"] == "SEWER"
    assert c["Septic / Sewer"] == "Likely SEPTIC - verify"


def test_xome_bids():
    from datetime import date
    from sources import xome
    page = ('x = "[{\\"listingID\\":1,\\"streetAddress\\":\\"75 Windemere Street\\",\\"city\\":\\"Springfield\\",'
            '\\"auctionInfo\\":{\\"bidType\\":\\"Starting Bid \\",\\"auctionStartDate\\":\\"2026-10-27T11:00:00+00:00\\",'
            '\\"bidAmount\\":\\"$$187,500\\"}},'
            '{\\"listingID\\":2,\\"streetAddress\\":\\"67 Marmon Street\\",\\"city\\":\\"Springfield\\",'
            '\\"auctionInfo\\":{\\"bidType\\":\\"Est. Opening Bid \\",\\"auctionStartDate\\":\\"2026-12-09T11:00:00+00:00\\",'
            '\\"bidAmount\\":\\"$$98,599.98\\"}},'
            '{\\"listingID\\":3,\\"streetAddress\\":\\"1 Elm St\\",\\"city\\":\\"Springfield\\",'
            '\\"auctionInfo\\":{\\"bidType\\":\\"Est. Opening Bid \\",\\"auctionStartDate\\":\\"2026-11-09T11:00:00+00:00\\",'
            '\\"bidAmount\\":\\"$$0\\"}},'
            '{\\"listingID\\":4,\\"streetAddress\\":\\"220 Antelope Drive\\",\\"city\\":\\"Lee\\",'
            '\\"auctionInfo\\":{\\"bidType\\":\\"Final Bid\\",\\"auctionStartDate\\":\\"2026-10-24T13:00:00+00:00\\",'
            '\\"bidAmount\\":\\"$$147,000\\"}},'
            '{\\"listingID\\":5,\\"streetAddress\\":\\"6 Harvard Ave\\",\\"city\\":\\"Saugus\\",'
            '\\"auctionInfo\\":{\\"bidType\\":\\"Starting Bid \\",\\"auctionStartDate\\":\\"2026-11-14T14:00:00+00:00\\",'
            '\\"bidAmount\\":\\"$$340,000\\"}}]"; {\\"totalPages\\":2}')
    rows = xome.parse_page(page, date(2026, 10, 26))
    assert [(a, b) for a, _, b, _ in rows] == [("75 Windemere Street", "$187,500 (Xome)"),
                                                ("67 Marmon Street", "$98,600 est. (Xome)")]
    assert xome.total_pages(page) == 2


def test_owner_names_every_borrower():
    from massgis import last_names, owner_matches
    pats = owner_patterns("Susan J. Boyer and Ronald C. Boyer")
    assert "BOYER RONALD" in pats and "BOYER SUSAN" in pats
    assert owner_matches("BOYER RONALD C", pats)
    assert owner_matches("KABAMBA,CHRISTOPHORE M", owner_patterns("Christophore M. Kabamba"))
    assert not owner_matches("BOYERS RONALD", ["BOYER, RONALD"])
    assert last_names("Christine Diane MacDonald, Deilya Nap") == {"MACDONALD", "NAP"}
    assert last_names("Prestigious One LLC") == set()


def test_street_word_and_folding():
    from tracker import _street_phrases, _street_word, fold_into_listings
    assert _street_phrases("29 HIGH ST N") == ("29", ["High Street", "High St"])
    assert _street_phrases("46 NEW BOSTON ROAD") == ("46", ["New Boston Road"])
    assert _street_phrases("11 RICE LN #3") == ("11", ["Rice Lane", "Rice Ln"])
    assert _street_phrases("0 MEADOW ST") == ("", [])
    assert _street_phrases("10 Wilson Graves Rd.") == ("10", ["Wilson Graves Road", "Wilson Graves Rd"])
    assert _street_word("22 SORRENTO ST") == "SORRENTO"
    assert _street_word("46 NEW BOSTON ROAD") == "BOSTON"
    assert _street_word("11 RICE LN #3") == "RICE"
    listing = {"address": "11-3 Rice Lane", "town": "Worcester", "auction_date": "2026-10-29", "gis": {}}
    notice = {"address": "11 Rice Ln #3", "town": "Worcester", "auction_date": "2026-10-29",
              "gis": {"parcel_id": "P1"}, "notices": [{"n": 1}]}
    other = {"address": "5 Elm St", "town": "Worcester", "gis": {}, "notices": [{"n": 2}]}
    auctions = {"L": listing}
    left = fold_into_listings(auctions, {"N-a": notice, "N-b": other})
    assert list(left) == ["N-b"] and listing["notices"] == [{"n": 1}]


def test_typed_address_on_notice_row():
    from tracker import use_typed_addresses
    recs = {"N-1": {"address": "", "town": ""}, "N-2": {"address": "", "town": ""}, "N-3": {"address": "", "town": ""}}
    typed = [{"Key": "N-1", "Address": "14 Oak St", "Town": "Ware"},
             {"Key": "N-2", "Address": "22 Sorrento St", "Town": "Springfield"},
             {"Key": "N-3", "Address": "(borrower: Jane Doe)", "Town": ""}]
    previous = {"N-2": {"address": "22 Sorrento St"}}
    use_typed_addresses(recs, typed, {"Ware", "Springfield"}, previous)
    assert (recs["N-1"]["address"], recs["N-1"]["town"]) == ("14 Oak St", "Ware")
    assert recs["N-2"]["address"] == "" and recs["N-3"]["address"] == ""


def test_posnik_status_in_date_cell():
    """Posnik puts a sale's status above its weekday; it must not slide onto the next sale."""
    from datetime import date
    from sources.auction_sites import posnik

    def row(date_cell, town, addr, page):
        return (f'<tr><td><a href="../html26/{page}.htm">{date_cell}</a></td>'
                f'<td><a href="../html26/{page}.htm">{town}, MA</a></td>'
                f'<td><a href="../html26/{page}.htm">3 BEDROOM HOME <u>{addr}</u></a></td></tr>')
    html = "<table>" + "".join([
        row("<span>POSTPONED</span><u>WEDNESDAY</u><br>OCTOBER 14TH", "CHICOPEE", "193 Fairview Avenue", "101426"),
        row("<u>THURSDAY</u><br>OCTOBER 15TH<br>AT 11:00 AM", "LEEDS", "47 East Center Street", "101526"),
        row("<span>CANCELLED</span><u>WEDNESDAY</u><br>OCTOBER 28TH", "CHICOPEE", "193 Fairview Avenue", "101426"),
        row("<span>POSTPONED</span><u>WEDNESDAY</u><br>OCTOBER 21ST<br>Please\xa0See<br>NOVEMBER\xa013TH",
            "SPRINGFIELD", "57 Pearl Street", "102126"),
    ]) + "</table>"

    class Resp:
        text, url = html, "https://posnik.com/cals/_recal.htm"

    class Http:
        def get(self, url, **kw):
            return Resp()
    recs = posnik(Http(), {"name": "Posnik", "url": Resp.url}, date(2026, 10, 10))
    got = [(r["address"], r["auction_date"], r["status"]) for r in recs]
    assert got == [("193 Fairview Avenue", "2026-10-14", "Postponed"),
                   ("47 East Center Street", "2026-10-15", "Scheduled"),
                   ("193 Fairview Avenue", "2026-10-28", "Cancelled"),
                   ("57 Pearl Street", "2026-11-13", "Postponed")]
    assert recs[3]["original_date"] == "2026-10-21"


class FakeCalendar:
    """Just enough of the Calendar API's events endpoint for calendar_sync."""

    def __init__(self):
        self.events, self.calls = {}, []

    class Resp:
        def __init__(self, code, data=None):
            self.status_code, self._data = code, data or {}

        def json(self):
            return self._data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

    def get(self, url, params=None, **kw):
        return self.Resp(200, {"items": list(self.events.values())})

    def post(self, url, json=None, **kw):
        self.calls.append(("insert", json["id"]))
        if json["id"] in self.events:
            return self.Resp(409)
        self.events[json["id"]] = dict(json)
        return self.Resp(200)

    def put(self, url, json=None, **kw):
        eid = url.rsplit("/", 1)[-1]
        self.calls.append(("update", eid))
        self.events[eid] = {**json, "id": eid}
        return self.Resp(200)

    def delete(self, url, **kw):
        eid = url.rsplit("/", 1)[-1]
        self.calls.append(("delete", eid))
        self.events[eid]["status"] = "cancelled"
        return self.Resp(204)


def test_calendar_sync():
    from datetime import date
    import calendar_sync as C
    today = date(2026, 10, 29)
    row = lambda key, st, d, t="11:00 AM": {"Key": key, "Status": st, "Auction Date": d, "Time": t,
                                            "Address": f"{key} Main St", "Town": "Ware", "Deposit": "$5,000"}
    cal = FakeCalendar()
    rows = [row("1", "Scheduled", "11/02/2026"), row("2", "Notice only", "11/03/2026", ""),
            row("3", "Cancelled", "11/04/2026"), row("4", "Scheduled", "10/20/2026")]
    res = C.sync(rows, "cal", "sheet", today, cal)
    assert (res["events"], res["added"]) == (2, 2)
    e1, e2 = cal.events[C.event_id("1")], cal.events[C.event_id("2")]
    assert e1["start"] == {"dateTime": "2026-11-02T11:00:00", "timeZone": C.TZ} and "Deposit: $5,000" in e1["description"]
    assert e2["start"] == {"date": "2026-11-03"} and e2["summary"].endswith("(legal notice only)")
    # Nothing changed: nothing written.
    cal.calls.clear()
    assert C.sync(rows, "cal", "sheet", today, cal)["updated"] == 0 and cal.calls == []
    # Sale 1 postponed, sale 2 cancelled, a past event left alone.
    cal.events["old"] = {"id": "old", "status": "confirmed", "start": {"date": "2026-10-28"}}
    rows = [row("1", "Postponed", "11/16/2026"), row("2", "Cancelled", "11/03/2026", "")]
    res = C.sync(rows, "cal", "sheet", today, cal)
    assert res["removed"] == 2 and cal.events["old"]["status"] == "confirmed"
    assert cal.events[C.event_id("1")]["status"] == "cancelled"
    # Sale 1 back on as scheduled: its event comes back.
    res = C.sync([row("1", "Scheduled", "11/16/2026")], "cal", "sheet", today, cal)
    assert res["updated"] == 1 and cal.events[C.event_id("1")]["status"] == "confirmed"


def test_zekos_municipal_and_foreclosure():
    from datetime import date
    import time
    from sources import auction_sites as A

    def block(href, title, badge, when, blurb):
        return (f'<div class="row"><div><div class="easy-sold-badge">{badge}</div></div><div>'
                f'<h3><a href="{href}">{title}</a></h3><ul><li><strong>{when}</strong></li></ul>'
                f'<div><p>{blurb}</p></div></div></div>')
    home = "<div>" + "".join([
        block("/auctions/detail/bw1", "City of Chicopee", "5 PROPERTIES", "October 29 at 2:00 PM EDT",
              "The auction will be held on Thursday, October 29, 2026 at 2:00 PM."),
        block("/auctions/detail/bw2", "Town of Spencer", "4 PROPERTIES!", "October 28 at 2:00 PM EDT",
              "will be held on Tuesday, October 28, 2025 at 2:00 PM."),
        block("/auctions/detail/bw3", "12 Pine Ridge Drive, Oxford", "SOLD", "July 31 at 11:00 AM EDT", "Foreclosure."),
    ]) + "</div>"
    detail = ("<p>The live auction will be held on Thursday, October 29, 2026 at 2:00 PM at The Hub at Market "
              "Square, 31 Springfield St., Chicopee, MA. A $10,000 deposit per property is required.</p>")

    class Resp:
        def __init__(self, text, url):
            self.text, self.url = text, url

    items = "".join(
        f'<div class="row"><div><a href="/auctions/item/bw{i}" title="{addr}"><img></a></div>'
        f'<div><h4><a href="/auctions/item/bw{i}">{addr}</a></h4><p>{kind}. Deposit $10,000. 30 Day Closing.</p>'
        f'<a href="/auctions/item/bw{i}">View Property Info &raquo;</a></div></div>'
        for i, (addr, kind) in enumerate([("141 Woodlawn Street", "Single Family Home"),
                                          ("0 Meadow Street", "Industrial Land Parcel")]))
    lists = {"items": items}

    class Http:
        def get(self, url, **kw):
            if "/ajax/items/" in url:
                return Resp(lists["items"], "https://www.zekosgroup.com/")
            return Resp(detail if "/detail/" in url else home, "https://www.zekosgroup.com/")
    sleep, time.sleep = time.sleep, lambda s: None
    try:
        site = {"name": "Zekos", "url": "https://www.zekosgroup.com/"}
        recs = A.zekos(Http(), site, date(2026, 10, 28))
        lists["items"] = ""  # property list unreadable: the sale stays one row
        fallback = A.zekos(Http(), site, date(2026, 10, 28))[0]
    finally:
        time.sleep = sleep
    woodlawn, meadow, spencer, oxford = recs
    assert (woodlawn["address"], woodlawn["town"], woodlawn["specs"]) == ("141 Woodlawn Street", "Chicopee", "Single Family Home")
    assert (woodlawn["auction_date"], woodlawn["auction_time"], woodlawn["deposit"]) == ("2026-10-29", "2:00 PM", "$10,000")
    assert woodlawn["status_note"].startswith("Municipal (town-owned / tax title) auction (1 of 2)")
    assert "31 Springfield St., Chicopee, MA" in woodlawn["status_note"] and meadow["specs"] == "Industrial Land Parcel"
    assert fallback["address"] == "(municipal auction - City of Chicopee)" and fallback["specs"].startswith("5 town-owned")
    assert spencer["auction_date"] == "2025-10-28"  # last year's sale still on the page
    assert (oxford["address"], oxford["town"], oxford["status"]) == ("12 Pine Ridge Drive", "Oxford", "Sold")


def test_untracked_notice_towns():
    from tracker import _may_be_tracked
    towns = {"Grafton", "Greenfield"}
    note = lambda *t: {"notices": [{"probe_towns": list(t)}]}
    assert not _may_be_tracked(note("Fitchburg", "Worcester"), towns)
    assert _may_be_tracked(note("Grafton", "Worcester"), towns)
    assert _may_be_tracked(note(), towns)  # nothing to go on: keep it

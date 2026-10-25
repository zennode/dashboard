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

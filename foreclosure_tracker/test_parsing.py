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

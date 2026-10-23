"""Massachusetts towns for the counties we track, plus common village names."""

COUNTY_TOWNS = {
    "Berkshire": [
        "Adams", "Alford", "Becket", "Cheshire", "Clarksburg", "Dalton", "Egremont",
        "Florida", "Great Barrington", "Hancock", "Hinsdale", "Lanesborough", "Lee",
        "Lenox", "Monterey", "Mount Washington", "New Ashford", "New Marlborough",
        "North Adams", "Otis", "Peru", "Pittsfield", "Richmond", "Sandisfield", "Savoy",
        "Sheffield", "Stockbridge", "Tyringham", "Washington", "West Stockbridge",
        "Williamstown", "Windsor",
    ],
    "Franklin": [
        "Ashfield", "Bernardston", "Buckland", "Charlemont", "Colrain", "Conway",
        "Deerfield", "Erving", "Gill", "Greenfield", "Hawley", "Heath", "Leverett",
        "Leyden", "Monroe", "Montague", "New Salem", "Northfield", "Orange", "Rowe",
        "Shelburne", "Shutesbury", "Sunderland", "Warwick", "Wendell", "Whately",
    ],
    "Hampden": [
        "Agawam", "Blandford", "Brimfield", "Chester", "Chicopee", "East Longmeadow",
        "Granville", "Hampden", "Holland", "Holyoke", "Longmeadow", "Ludlow", "Monson",
        "Montgomery", "Palmer", "Russell", "Southwick", "Springfield", "Tolland", "Wales",
        "West Springfield", "Westfield", "Wilbraham",
    ],
    "Hampshire": [
        "Amherst", "Belchertown", "Chesterfield", "Cummington", "Easthampton", "Goshen",
        "Granby", "Hadley", "Hatfield", "Huntington", "Middlefield", "Northampton",
        "Pelham", "Plainfield", "South Hadley", "Southampton", "Ware", "Westhampton",
        "Williamsburg", "Worthington",
    ],
    "Worcester": [
        "Ashburnham", "Athol", "Auburn", "Barre", "Berlin", "Blackstone", "Bolton",
        "Boylston", "Brookfield", "Charlton", "Clinton", "Douglas", "Dudley",
        "East Brookfield", "Fitchburg", "Gardner", "Grafton", "Hardwick", "Harvard",
        "Holden", "Hopedale", "Hubbardston", "Lancaster", "Leicester", "Leominster",
        "Lunenburg", "Mendon", "Milford", "Millbury", "Millville", "New Braintree",
        "North Brookfield", "Northborough", "Northbridge", "Oakham", "Oxford", "Paxton",
        "Petersham", "Phillipston", "Princeton", "Royalston", "Rutland", "Shrewsbury",
        "Southborough", "Southbridge", "Spencer", "Sterling", "Sturbridge", "Sutton",
        "Templeton", "Upton", "Uxbridge", "Warren", "Webster", "West Boylston",
        "West Brookfield", "Westborough", "Westminster", "Winchendon", "Worcester",
    ],
}

# Village / postal names that listings use instead of the municipality name.
VILLAGES = {
    "Indian Orchard": "Springfield", "Sixteen Acres": "Springfield",
    "Feeding Hills": "Agawam", "Florence": "Northampton", "Leeds": "Northampton",
    "Bay State Village": "Northampton", "Turners Falls": "Montague",
    "Millers Falls": "Montague", "Montague Center": "Montague",
    "Lake Pleasant": "Montague", "Shelburne Falls": "Shelburne",
    "South Deerfield": "Deerfield", "Whitinsville": "Northbridge",
    "Linwood": "Northbridge", "Rockdale": "Northbridge", "Three Rivers": "Palmer",
    "Thorndike": "Palmer", "Bondsville": "Palmer", "Housatonic": "Great Barrington",
    "Fiskdale": "Sturbridge", "Jefferson": "Holden", "Cherry Valley": "Leicester",
    "Rochdale": "Leicester", "Baldwinville": "Templeton", "East Templeton": "Templeton",
    "Gilbertville": "Hardwick", "Wheelwright": "Hardwick", "Haydenville": "Williamsburg",
    "North Grafton": "Grafton", "South Grafton": "Grafton", "Fayville": "Southborough",
    "Manchaug": "Sutton", "South Lancaster": "Lancaster",
    "East Douglas": "Douglas", "North Oxford": "Oxford", "North Uxbridge": "Uxbridge",
    "Fisherville": "Grafton", "Farnumsville": "Grafton", "Westover": "Chicopee",
    "Aldenville": "Chicopee", "Willimansett": "Chicopee", "Fairview": "Chicopee",
    "Ware Center": "Ware",
    "Southfield": "New Marlborough", "Mill River": "New Marlborough",
    "Glendale": "Stockbridge", "Interlaken": "Stockbridge", "Lenox Dale": "Lenox",
    "East Lee": "Lee", "South Lee": "Lee", "Hinsdale Village": "Hinsdale",
    "Ashley Falls": "Sheffield", "North Egremont": "Egremont",
    "South Egremont": "Egremont", "Drury": "Florida", "Blackinton": "Williamstown",
    "Charlemont Village": "Charlemont", "Orange Center": "Orange",
    "Woronoco": "Russell", "Huntington Village": "Huntington",
    "North Amherst": "Amherst", "South Amherst": "Amherst",
    "Holyoke Range": "South Hadley", "South Hadley Falls": "South Hadley",
    "East Otis": "Otis", "Otis Village": "Otis", "Whalom": "Lunenburg",
    "East Princeton": "Princeton", "South Barre": "Barre", "Barre Plains": "Barre",
    "Wheelwright Village": "Hardwick", "Coldbrook Springs": "Oakham",
}

# MassGIS TOWN_ID for each town (the parcel layer answers much faster on this field).
TOWN_IDS = {
    "Adams": 4, "Agawam": 5, "Alford": 6, "Amherst": 8, "Ashburnham": 11, "Ashfield": 13,
    "Athol": 15, "Auburn": 17, "Barre": 21, "Becket": 22, "Belchertown": 24, "Berlin": 28,
    "Bernardston": 29, "Blackstone": 32, "Blandford": 33, "Bolton": 34, "Boylston": 39,
    "Brimfield": 43, "Brookfield": 45, "Buckland": 47, "Charlemont": 53, "Charlton": 54,
    "Cheshire": 58, "Chester": 59, "Chesterfield": 60, "Chicopee": 61, "Clarksburg": 63,
    "Clinton": 64, "Colrain": 66, "Conway": 68, "Cummington": 69, "Dalton": 70,
    "Deerfield": 74, "Douglas": 77, "Dudley": 80, "East Brookfield": 84,
    "East Longmeadow": 85, "Easthampton": 87, "Egremont": 90, "Erving": 91,
    "Fitchburg": 97, "Florida": 98, "Gardner": 103, "Gill": 106, "Goshen": 108,
    "Grafton": 110, "Granby": 111, "Granville": 112, "Great Barrington": 113,
    "Greenfield": 114, "Hadley": 117, "Hampden": 120, "Hancock": 121, "Hardwick": 124,
    "Harvard": 125, "Hatfield": 127, "Hawley": 129, "Heath": 130, "Hinsdale": 132,
    "Holden": 134, "Holland": 135, "Holyoke": 137, "Hopedale": 138, "Hubbardston": 140,
    "Huntington": 143, "Lancaster": 147, "Lanesborough": 148, "Lee": 150, "Leicester": 151,
    "Lenox": 152, "Leominster": 153, "Leverett": 154, "Leyden": 156, "Longmeadow": 159,
    "Ludlow": 161, "Lunenburg": 162, "Mendon": 179, "Middlefield": 183, "Milford": 185,
    "Millbury": 186, "Millville": 188, "Monroe": 190, "Monson": 191, "Montague": 192,
    "Monterey": 193, "Montgomery": 194, "Mount Washington": 195, "New Ashford": 200,
    "New Braintree": 202, "New Marlborough": 203, "New Salem": 204, "North Adams": 209,
    "North Brookfield": 212, "Northampton": 214, "Northborough": 215, "Northbridge": 216,
    "Northfield": 217, "Oakham": 222, "Orange": 223, "Otis": 225, "Oxford": 226,
    "Palmer": 227, "Paxton": 228, "Pelham": 230, "Peru": 233, "Petersham": 234,
    "Phillipston": 235, "Pittsfield": 236, "Plainfield": 237, "Princeton": 241,
    "Richmond": 249, "Rowe": 253, "Royalston": 255, "Russell": 256, "Rutland": 257,
    "Sandisfield": 260, "Savoy": 263, "Sheffield": 267, "Shelburne": 268,
    "Shrewsbury": 271, "Shutesbury": 272, "South Hadley": 275, "Southampton": 276,
    "Southborough": 277, "Southbridge": 278, "Southwick": 279, "Spencer": 280,
    "Springfield": 281, "Sterling": 282, "Stockbridge": 283, "Sturbridge": 287,
    "Sunderland": 289, "Sutton": 290, "Templeton": 294, "Tolland": 297, "Tyringham": 302,
    "Upton": 303, "Uxbridge": 304, "Wales": 306, "Ware": 309, "Warren": 311,
    "Warwick": 312, "Washington": 313, "Webster": 316, "Wendell": 319,
    "West Boylston": 321, "West Brookfield": 323, "West Springfield": 325,
    "West Stockbridge": 326, "Westborough": 328, "Westfield": 329, "Westhampton": 331,
    "Westminster": 332, "Whately": 337, "Wilbraham": 339, "Williamsburg": 340,
    "Williamstown": 341, "Winchendon": 343, "Windsor": 345, "Worcester": 348,
    "Worthington": 349,
}

TOWN_TO_COUNTY = {t: c for c, towns in COUNTY_TOWNS.items() for t in towns}


def resolve_town(name):
    """Return (town, county) for a town or village name, or (None, None)."""
    if not name:
        return None, None
    key = " ".join(name.replace(".", " ").split()).title()
    if key in TOWN_TO_COUNTY:
        return key, TOWN_TO_COUNTY[key]
    town = VILLAGES.get(key)
    if town and town in TOWN_TO_COUNTY:
        return town, TOWN_TO_COUNTY[town]
    return None, None


def all_place_names():
    """Every town and village name we can recognise, longest first."""
    names = list(TOWN_TO_COUNTY) + [v for v, t in VILLAGES.items() if t in TOWN_TO_COUNTY]
    return sorted(set(names), key=len, reverse=True)

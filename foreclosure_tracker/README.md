# Foreclosure Auction Tracker (Berkshire, Hampden, Hampshire, Franklin, Worcester)

Checks every auctioneer website on your **MA - Auction Site Links** tab, plus the
newspapers' foreclosure legal notices, five times a day. Then it updates your
**MA Weekly Auctions** Google Sheet (set by `spreadsheet_id` in `config.yaml`).

It writes only to its own tabs. Any other tabs in the sheet are never touched.

| Tab | What's on it |
| --- | --- |
| **Auto - Auction Tracker** | One row per auction in your towns. Columns follow your weekly tabs: mortgage, year, lender, 1st/2nd, date, time, address, liens, specs, year built, lot, acres, living area, septic/sewer, deposit, auction company. After those come status, owner, parcel and links. Scheduled auctions and notice-only sales (blue). |
| **Auto - Cancelled & Postponed** | Same columns, for auctions that were cancelled, postponed, sold or dropped off their site. Rows move between the two tabs as their status changes, and your notes move with them. |
| **Auto - Owner Call List** | Owners worth calling, ranked by priority, with the reason to call. It includes **pre-foreclosure** owners from Land Court filings, months before an auction is set. Your Phone / Called? / Call Date / Outcome / Call Notes columns are kept between runs. |
| **Auto - Status Changes** | A running log of every new auction, postponement, cancellation, time change, and listing that drops off a site. |
| **Auto - Source Health** | Each site's result on the last run, so you know which sites to spot-check by hand. |

## Where each column comes from

| Column | Source |
| --- | --- |
| Auction date, time, status, deposit, auction company | The auctioneer's site. If several sites list the same property, the latest date wins. |
| Lender, mortgage year, borrower, 1st/2nd, Book/Page | The legal notice. It's matched to the listing by registry Book/Page, address, or owner name. |
| Mortgage amount | Almost never published: only 1 of 722 recent notices stated the loan amount. It's on the recorded mortgage, so use the **Registry Book/Page** and **Registry Search** link. masslandrecords.com blocks automated access, so we don't scrape it. Whatever you type in the column is kept. |
| Owner, mailing address, owner-occupied, year built, living area, building area, lot sq ft / acres, style, rooms, assessed value, last sale, parcel ID | MassGIS statewide assessor tables (free, official, updated yearly). A local copy is refreshed monthly. |
| Septic / Sewer | The listing text if it says so. Otherwise "Likely SEPTIC - verify" for small towns listed in `config.yaml`, or "Verify w/ BOH". |
| Tax / Water / Sewer Liens | Yours to fill in. Town tax data isn't online in any standard form. What you type is kept. |
| Team Notes | Yours. Always kept. |

**Columns you type into are safe.** If you fill in Mortgage Amount, Liens, Septic, Specs,
1st/2nd, Lender or Mortgage Year, your value is kept whenever the scraper has nothing
for that column.

**"Notice only" rows** are foreclosures that have a legal notice but aren't on any
auction site, often because the auctioneer has no website. The full notice is behind a
CAPTCHA, but the notice site's search covers the full text. Once a day the tracker searches
it for every upcoming sale date, common deposits, auctioneer names and town names, and
fills in the sale date, deposit, auctioneer and town from what each notice matches. When
the excerpt has no address, it finds the borrower's property in the assessor records for
the towns the notice names. The sale time isn't found this way; click **Legal Notice** for it.

## Mortgage amount, year and 1st/2nd from PropStream

Legal notices almost never state the loan amount, and the registry site blocks automated
access. PropStream has the recorded mortgages, so the tracker can use it in four steps:

1. Every run, the tracker lists the auctions still missing a mortgage amount on the
   **Auto - PropStream Lookup** tab (address, city, state).
2. In PropStream, choose **Import List** and upload that tab (File → Download → CSV).
3. Export the imported list. Include the mortgage columns: amount, recording date and
   lender for each loan, and estimated equity.
4. Paste the export, header row included, into a tab named **PropStream Export**. On the
   next run the tracker fills **Mortgage Amount**, **Mortgage Year**, **Lender**,
   **1st or 2nd** and **Est. Equity**.

It matches PropStream's loans to the foreclosing mortgage by year. If the notice's
mortgage is PropStream's second loan, the row says **2nd**. If there's also a second loan
behind the one being foreclosed, it says **1st (2nd exists)**. Values from the legal
notice, or typed in by hand, are never overwritten. Repeat steps 2–4 whenever the lookup
tab grows (about once a week).

## One-time setup (about 15 minutes)

1. **Create a Google service account**
   - Go to <https://console.cloud.google.com/> and create a project (any name).
   - Enable the **Google Sheets API**: APIs & Services → Library → "Google Sheets API" → Enable.
   - Go to APIs & Services → Credentials → Create credentials → **Service account**, and name it `auction-tracker`.
   - Open the service account, go to Keys → Add key → JSON. A `.json` file downloads.
2. **Share the sheet** with the service account's email (it looks like
   `auction-tracker@your-project.iam.gserviceaccount.com`) as an **Editor**.
3. **Add the key to GitHub**: open this repo, go to Settings → Secrets and variables →
   Actions → New repository secret. Name it `GOOGLE_SERVICE_ACCOUNT_JSON` and paste the
   whole contents of the `.json` file.
4. **Run it once by hand**: go to Actions → *Foreclosure auction tracker* → Run workflow.
   The first run takes about 5 minutes because it downloads the assessor tables for
   your towns (about 55 MB, refreshed monthly). Later runs take 2-3 minutes.

After that it runs on its own at about 7:15am, 10:15am, 12:15pm, 3:15pm and 6:15pm
Eastern. To change the times, edit the `cron` line in
`.github/workflows/foreclosure-tracker.yml`.

## Changing what it tracks

Edit `config.yaml`:

- `towns:` limits tracking to certain towns, e.g. `towns: [Springfield, Chicopee, Holyoke]`.
  Leave it empty to track all 161 towns in the five counties.
- `auction_sites:` holds the auctioneer list. To add a site, add a line with its name and
  the URL of its schedule page. The generic reader handles most table- or list-style pages.
- `likely_septic_towns:` is the list of towns that get "Likely SEPTIC - verify".

## Run it on your own computer

```bash
cd foreclosure_tracker
pip install -r requirements.txt
python tracker.py --csv out/        # writes CSVs to out/, no Google login needed
GOOGLE_SERVICE_ACCOUNT_JSON="$(cat key.json)" python tracker.py   # updates the sheet
```

## Known gaps

Check **Auto - Source Health** after the first run. These sites can't be read
automatically and need a manual look:

- **JavaScript-only sites:** Hudson & Marshall, Tranzon, AuctionNetwork and Harmon's own
  page. Harmon's sales also show up through Commonwealth. ServiceLink is read through its
  data feed.
- **Sites that block automated visitors or are down:** Jump and Biffway (403), American
  Auctions (404, the site is gone), and Sutton and Strategic Auction (no connection, from
  GitHub as well). Their sales still show up as notice-only rows when a legal notice runs.
- **Facebook:** the Marcella page can't be read.

## A note on calling owners

Massachusetts regulates "foreclosure rescue" offers (940 CMR 25.00 and M.G.L. c.93A).
Federal and state do-not-call rules also apply to cold calls. Check your scripts and any
offers with your attorney before you start calling.

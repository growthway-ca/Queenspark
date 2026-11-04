# Queen's Park Watch: vote history scraper

A dataset of every recorded division (recorded vote) in the Legislative
Assembly of Ontario from the start of the 42nd Parliament (July 2018) to
today, plus every bill Doug Downey sponsored. It feeds the Queen's Park
Watch page, which imports votes as JSON on its Desk.

## Current dataset (built 2026-11-04)

| Parliament | Votes | From vote search | From V&P | Totals-only (excluded) |
| --- | --- | --- | --- | --- |
| 42nd | 384 | 0 | 384 | 19 |
| 43rd | 205 | 132 | 73 | 22 |
| 44th | 116 | 77 | 39 | 4 |

- Unmatched names: **0**.
- Every roll equals the totals printed in its source.
- `curated.json` has 24 votes (114 KB); `downey-bills.json` has 10 bills.
- Today's (4 Nov 2026) Votes and Proceedings page wasn't published yet; the
  next run will pick it up.

## Quick start

```sh
pip install -r requirements.txt
python -m scraper.build            # fetch anything new, rebuild everything
python -m scraper.checks           # acceptance checks and a summary
```

A first run from an empty cache makes about 2,500 requests at one per
second, so it takes roughly 45 minutes. Later runs only fetch pages that
aren't cached yet, such as new sitting days and bills.

Useful options:

| Command | What it does |
| --- | --- |
| `python -m scraper.build --offline` | Rebuild from `cache/` only; fails if a page is missing |
| `python -m scraper.build --parliaments 44` | Rebuild one parliament (skips `downey-bills.json` and `curated.json`) |
| `python -m scraper.build --refresh-rosters` | Re-read member pages into `data/roster-p<N>.json` |
| `python -m scraper.members 42 43 44` | Rebuild the rosters only |

### Picking up new votes

Pages are cached forever, so a page that changes on ola.org keeps its old
copy until you delete it from `cache/`. To pick up new sitting days, bills
and votes, delete these listing pages first:

```sh
rm -f cache/www.ola.org/en/legislative-business/house-documents.html \
      cache/www.ola.org/en/legislative-business/house-documents/parliament-44/session-1.html \
      cache/www.ola.org/en/legislative-business/bills/parliament-44/session-1.html
rm -f cache/www.ola.org/en/legislative-business/bills/parliament-44/session-1/bill-*.html
python -m scraper.build
```

The bill pages are deleted too because their status table and links to new
votes change as a bill moves through the House. A Votes and Proceedings page
that wasn't published yet is never cached, so it is fetched on the next run.
For a new session, delete the house-documents index and the new session
appears automatically.

## Fetching rules

- One request per second, shared across all scraper processes through a
  lock file (`cache/.ratelimit`).
- User-Agent: `QueensParkWatch-scraper/0.1 (...; +https://github.com/growthway-ca/queenspark)`.
- robots.txt is honoured, including its wildcard rules. ola.org disallows
  every URL with a query string (`Disallow: /*?`), so the vote-search
  *results* pages are never used. Vote pages are found through the links on
  bill pages instead.
- Every raw page is cached under `cache/`, which git ignores.

## Sources and how they are combined

| Parliament | Rolls come from | Notes |
| --- | --- | --- |
| 42nd (2018-22, sessions 1 and 2) | Votes and Proceedings | Vote search doesn't cover it |
| 43rd (2022-25) | Vote search, plus V&P for motions | |
| 44th (2025-) | Vote search, plus V&P for motions | |

Vote search only covers the 43rd Parliament onward (its own page says
"from the 43rd Parliament to the present").

For each parliament the build:

1. Reads the sessions from the house-documents index and every bill page in
   each session (sponsor, status history, links to vote pages).
2. Reads every sitting day's Votes and Proceedings (V&P) and extracts every
   recorded division. This is the complete list of votes. ola.org has used
   four different HTML layouts for V&P since 2018, and all are handled
   (`scraper/parse.py`).
3. Names each division (`scraper/classify.py`): bill readings, committee
   reports, time allocation, closure (including Standing Order 51 "that
   this question be now put"), opposition day motions, private members'
   motions, the budget motion, the Address in Reply, adjournment motions,
   leave to introduce a bill, government motions and amendments.
4. 43rd and 44th: matches each vote-search page to its V&P division on the
   same date with the same totals, and uses the page's roll.
5. Every other division gets its roll from the V&P surname lists, matched
   against that parliament's roster (`scraper/matcher.py`).

### Rosters and name matching

`data/roster-p<N>.json` is built from `/en/members/parliament-<N>` and each
member's page. It holds the riding, party affiliations and career roles,
each with start and end dates. Matching a V&P name:

- `Downey` matches on surname alone; `Smith (Peterborough—Kawartha)` matches
  on surname and riding. Accents and dash styles are ignored.
- Only members sitting on the vote date are candidates, which handles
  by-elections and resignations.
- The match must be unique. Otherwise the name goes to `data/unmatched.csv`
  and is left out of the roll. The current run has **no unmatched names**.
- `scraper/aliases.json` lists the three cases checked by hand: Natalia
  Kusendova (now Kusendova-Bashta, in the 42nd and early 43rd) and the V&P
  misspelling "Cuzzeto" for Rudy Cuzzetto.

### Party and the minister flag on the vote date

Party, riding and the minister flag in every roll come from the roster *on
the vote date*, including for votes taken from vote-search pages. Those
pages show each member's **current** party and styling. For example, Chris
Scott appears as Independent on May 2025 votes, although he sat as a PC
until 22 September 2025. Brian Saunderson, Michelle Cooper and Matthew Rae
appear as "Hon." on votes from before they joined cabinet in September 2026.
The build corrects these and prints how many rows it changed.

The last roll field is `1` when the member held a cabinet post that day:
Premier, Deputy Premier, Minister, Associate Minister, Attorney General,
Solicitor General, Associate Attorney or Solicitor General, President of
the Treasury Board, Chair of Cabinet, or Government House Leader.
Parliamentary assistants, whips and committee chairs don't count, and
neither do former ministers. ola.org still styles some former ministers
"Hon." (for example Ernie Hardeman and Laurie Scott).

Members of the New Blue Party (Belinda Karahalios) and the Ontario Party
(Rick Nicholls) are coded `Ind`. Neither party had recognized status in
the House, so they sat as independents.

## Output files (`data/`)

| File | Contents |
| --- | --- |
| `votes-p42.json`, `votes-p43.json`, `votes-p44.json` | The vote dataset (format below) |
| `curated.json` | Subset for the artifact page (see below) |
| `downey-bills.json` | Every bill Doug Downey sponsored |
| `bills-p<N>.json` | Every bill: sponsors, status history, vote links |
| `roster-p<N>.json` | Members with dated ridings, parties and roles |
| `meta-p<N>.json` | Per vote: printed totals, session, V&P source, roll source |
| `totals-only-p<N>.json` | Recorded divisions printed without a usable names list (not in the vote files) |
| `unmatched.csv` | V&P names that couldn't be matched to exactly one member |

Vote format, as specified in CLAUDE.md:

```json
{
  "id": "bill-76-third-reading-2025-12-09",
  "bill": "Bill 76",
  "title": "Barrie – Oro-Medonte – Springwater Boundary Adjustment Act, 2025",
  "stage": "Third reading",
  "date": "2025-12-09",
  "source": "https://www.ola.org/...",
  "roll": [["Doug Downey", "PC", "Barrie—Springwater—Oro-Medonte", "y", 1]]
}
```

- `bill` is `Bill N` for bills. For motions it is a short name, such as
  `Time allocation (Bills 45, 72, 76)`, `Closure (Bill 5)`,
  `Opposition Day motion`, `Private Members' Motion No. 4`,
  `Budget motion`, `Address in Reply`, `Adjournment of the House` or
  `Government motion`.
- `stage` is for example `Second reading`, `Third reading`,
  `Adoption of committee report`, `Time allocation motion`,
  `Closure motion`, `Motion` or `Amendment (time allocation motion)`.
- `title` is the bill's short title, or for motions the mover and the
  operative text of the motion.
- `source` is the vote-search page when there is one; otherwise it is that
  day's Votes and Proceedings.

### Totals-only divisions

Some divisions are printed with totals but no usable list of names, so they
can't have a roll. They are kept out of the vote files and listed in
`totals-only-p<N>.json` with the reason:

- Most are adjournment motions recorded as "AYES - 20 NAYS - 60", or as
  "on the following division – Ayes 44, Nays 73".
- One is a tie recorded as "0 Ayes, 0 Nays", decided by the Speaker's
  casting vote.
- On 4 April 2019 (private member's Notice of Motion No. 42, Kitchener GO
  rail), both the HTML and the official PDF list only 12 of the 22 Ayes and
  26 of the 51 Nays, each printed twice.

Other source irregularities are handled and reported when the build runs:

- 28 Nov 2022: the Bill 4 result reads "Lost **of** the following
  division"; the parser accepts "of" as well as "on".
- Bill 35, third reading, 14 Nov 2022: the vote-search page shows 102–0, but
  V&P shows 100–0. The page's list includes the Speaker, Ted Arnott, who
  only votes to break a tie, and Kaleed Rasheed. The roll is taken from V&P,
  the official record.
- Name lists printed twice are de-duplicated, but only when the unique
  count then equals the printed total.
- On 4 April 2022 the "AYES" header is missing. The first names list is
  taken as the Ayes, because V&P always lists Ayes first.

## curated.json

The artifact page has a 16 MB limit, so it gets a subset. `curated.json`
holds every vote in the vote files whose bill is:

- sponsored by Doug Downey;
- named after a Simcoe County municipality in its short or long title; or
- listed in `data/tags.json`.

The Simcoe list is the sixteen lower-tier municipalities, the separated
cities of Barrie and Orillia, and "County of Simcoe" / "Simcoe County".
Names that are also ordinary words (Tiny, Tay, Essa, Severn, Midland,
Clearview) only count in the form "Township of X" or "Town of X".

A vote counts as "on a bill" when its `bill` is that bill, including time
allocation and closure motions that name it. Bill numbers restart each
session, so matching uses parliament, session and number.

### Tagging bills or votes

To add your own picks, create `data/tags.json` and rerun the build:

```json
{
  "bills": [{"parliament": 44, "session": 1, "bill": "Bill 60"}],
  "votes": ["opposition-day-motion-motion-2025-11-03"]
}
```

`bills` adds every vote on those bills; `votes` adds single votes by `id`.

## Importing curated.json into the page's Desk

1. Rebuild: `python -m scraper.build`, then `python -m scraper.checks`.
2. Open the Queen's Park Watch page and go to the **Desk**.
3. Use the Desk's JSON import, and choose or paste the contents of
   `data/curated.json`. The file is a JSON array of vote objects in the
   format above.
4. Imported votes are matched by `id`, so re-importing a newer
   `curated.json` should update existing votes rather than duplicate them,
   if the Desk de-duplicates by `id`. Check this the first time.

`curated.json` is far below the page's 16 MB limit (see the checks summary
for the current size). The full `votes-p*.json` files are for the planned
Netlify site.

## The explorer site

`site/index.html` is an interactive explorer of the full dataset: an
overview with key numbers and charts, a filterable vote list, each vote's
seat chart and roll, MPP profiles with party agreement and side-by-side
comparison, bills, Doug Downey's record (commitments, portfolio metrics,
riding money, sponsored bills), and notes on the data.

```sh
python -m scraper.site_data    # writes site/data.json and site/dist/
```

- `site/index.html` plus `site/data.json` is what gets published as the
  Claude artifact.
- `site/dist/` is a standalone copy for static hosting. On Netlify, set the
  publish directory to `site/dist`; there is no build command.
- `data/record.json` holds the sourced Government record items (court
  rulings, reversals, opposition-proposal pairings). Each has a timeline,
  the government's stated position and source links; bills are referenced
  as `parliament-session-number` because numbers repeat across sessions.
  Dates were cross-checked against the scraped bill pages.
- The editorial content (commitments, metrics, money, bill notes) lives in
  `data/editorial.json`. It was copied from the original Queen's Park Watch
  page; edit it there and rerun `site_data`.

## Checks

`python -m scraper.checks` runs:

- **Bill 76, third reading, 9 Dec 2025:** 70 for, 34 against, with Downey
  and Khanjin voting for.
- **Bill 245, third reading, 15 Apr 2021:** the roll equals the totals
  printed in that day's Votes and Proceedings.
- **Every vote:** the Ayes and Nays in the roll equal the totals printed in
  the source.
- **Schema:** unique ids, valid party codes and vote values.
- A **summary**: votes per parliament, unmatched names and file sizes.

## Code layout

| File | Role |
| --- | --- |
| `scraper/fetch.py` | Polite cached fetcher with a robots.txt matcher |
| `scraper/parse.py` | Parsers for index pages, vote-search pages, V&P (all layouts) and bill pages |
| `scraper/classify.py` | Names each division (bill or motion, title, stage) |
| `scraper/members.py` | Rosters from the members directory |
| `scraper/matcher.py` | V&P surname to member matching |
| `scraper/aliases.json` | Hand-checked surname aliases |
| `scraper/build.py` | The pipeline and all outputs |
| `scraper/checks.py` | Acceptance checks and summary |

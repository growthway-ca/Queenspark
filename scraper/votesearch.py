"""Scrape a parliament from the vote-search pages (43rd Parliament onward).

Vote-search result listings use query strings, which ola.org's robots.txt
disallows (`Disallow: /*?`). So vote pages are discovered without them:

  1. house-documents index      -> sessions in the parliament
  2. session bill list          -> every bill page
  3. each bill page             -> links to its vote-search pages
  4. each sitting day's Votes and Proceedings -> every recorded division

Step 4 is the completeness check: each V&P division is matched to a
vote-search page by date, bill number and totals. Divisions with no
matching page (mostly motions, which have no bill page to link from)
are written to data/uncovered-p<N>.json for a later pass.

Usage:  python -m scraper.votesearch 44 [--offline]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict

from .common import slug
from .fetch import BASE, ROOT, Fetcher
from .parse import (
    bill_links, parse_vote_page, parse_vp_divisions, sessions_from_house_docs_index,
    sitting_days, vote_links,
)

DATA = ROOT / "data"


def session_urls(p: int, s: int) -> dict[str, str]:
    return {
        "house_docs": f"/en/legislative-business/house-documents/parliament-{p}/session-{s}",
        "bills": f"/en/legislative-business/bills/parliament-{p}/session-{s}",
    }


def vp_url(p: int, s: int, day: str) -> str:
    return f"/en/legislative-business/house-documents/parliament-{p}/session-{s}/{day}/votes-proceedings"


def bill_number_from_label(label: str) -> str:
    return label.split()[-1].lower()  # "Bill 76" -> "76", "Bill Pr8" -> "pr8"


def scrape(parliament: int, fetcher: Fetcher) -> dict:
    index = fetcher.get("/en/legislative-business/house-documents")
    sessions = [s for p, s in sessions_from_house_docs_index(index) if p == parliament]
    print(f"Parliament {parliament}: sessions {sessions}", file=sys.stderr)

    vote_urls: set[str] = set()
    divisions = []
    problems: list[str] = []
    for s in sessions:
        u = session_urls(parliament, s)
        bills_html = fetcher.get(u["bills"])
        bills = bill_links(bills_html, parliament, s)
        print(f"  session {s}: {len(bills)} bills", file=sys.stderr)
        for b in bills:
            html = fetcher.get(b)
            if html is None:
                problems.append(f"bill page 404: {b}")
                continue
            vote_urls.update(v for v in vote_links(html) if f"/parliament-{parliament}/" in v)

        days = sitting_days(fetcher.get(u["house_docs"]), parliament, s)
        print(f"  session {s}: {len(days)} sitting days", file=sys.stderr)
        for day in days:
            url = vp_url(parliament, s, day)
            html = fetcher.get(url)
            if html is None:   # e.g. today's V&P not yet published
                problems.append(f"no Votes and Proceedings page yet: {BASE}{url}")
                continue
            divisions.extend(parse_vp_divisions(html, day, BASE + url))

    # -- vote pages -------------------------------------------------------
    pages = []
    for v in sorted(vote_urls):
        html = fetcher.get(v)
        if html is None:
            problems.append(f"vote page 404: {v}")
            continue
        page = parse_vote_page(html, BASE + v)
        for up in page.unknown_parties:
            problems.append(f"unknown party on {page.url}: {up}")
        ayes = sum(r[3] == "y" for r in page.roll)
        nays = sum(r[3] == "n" for r in page.roll)
        if (ayes, nays) != (page.ayes_total, page.nays_total):
            problems.append(
                f"roll/total mismatch on {page.url}: roll {ayes}-{nays}, printed {page.ayes_total}-{page.nays_total}"
            )
        pages.append(page)

    # -- reconcile with V&P ------------------------------------------------
    for d in divisions:
        if d.ayes_total < 0 or d.nays_total < 0:
            problems.append(f"V&P division without totals: {d.source} #{d.index}: {d.result_line!r}")
        elif d.names_listed and (len(d.ayes), len(d.nays)) != (d.ayes_total, d.nays_total):
            problems.append(
                f"V&P list/total mismatch {d.source} #{d.index}: lists {len(d.ayes)}-{len(d.nays)}, "
                f"printed {d.ayes_total}-{d.nays_total}"
            )

    matched_divs: set[int] = set()
    vp_index = {}

    def free(cands):
        return [d for d in cands if id(d) not in matched_divs]

    for page in pages:
        same_day = [
            d for d in divisions
            if d.date == page.date and (d.ayes_total, d.nays_total) == (page.ayes_total, page.nays_total)
        ]
        # Prefer a division whose subject names the same bill; otherwise accept
        # a division that is the only one that day with exactly these totals.
        cands = free([d for d in same_day if (d.bill_number or "").lower() == bill_number_from_label(page.bill)])
        if not cands and len(free(same_day)) == 1:
            cands = free(same_day)
        if not cands:
            problems.append(f"no V&P division matches vote page {page.url} ({page.date}, {page.ayes_total}-{page.nays_total})")
            continue
        d = cands[0]
        matched_divs.add(id(d))
        vp_index[page.url] = (d.date, d.index)

    uncovered = [
        {
            "date": d.date, "index": d.index, "bill": d.bill_number, "stage": d.stage,
            "subject": d.subject, "result": d.result_line,
            "ayes_total": d.ayes_total, "nays_total": d.nays_total,
            "names_listed": d.names_listed, "source": d.source,
        }
        for d in divisions if id(d) not in matched_divs
    ]

    # -- output -------------------------------------------------------------
    pages.sort(key=lambda p: vp_index.get(p.url, (p.date, 999)))
    seen = Counter()
    votes = []
    for page in pages:
        base_id = f"{slug(page.bill)}-{slug(page.stage)}-{page.date}"
        seen[base_id] += 1
        vid = base_id if seen[base_id] == 1 else f"{base_id}-{seen[base_id]}"
        votes.append({
            "id": vid,
            "bill": page.bill,
            "title": page.title,
            "stage": page.stage,
            "date": page.date,
            "source": page.url,
            "roll": page.roll,
        })
    return {"votes": votes, "uncovered": uncovered, "problems": problems,
            "divisions": len(divisions), "pages": pages}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("parliament", type=int)
    ap.add_argument("--offline", action="store_true", help="use cache only; fail on anything not cached")
    args = ap.parse_args(argv)

    fetcher = Fetcher(offline=args.offline)
    res = scrape(args.parliament, fetcher)
    DATA.mkdir(exist_ok=True)
    out = DATA / f"votes-p{args.parliament}.json"
    out.write_text(json.dumps(res["votes"], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    unc = DATA / f"uncovered-p{args.parliament}.json"
    unc.write_text(json.dumps(res["uncovered"], ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nParliament {args.parliament}")
    print(f"  live requests this run : {fetcher.live_requests}")
    print(f"  V&P divisions found    : {res['divisions']}")
    print(f"  vote-search pages      : {len(res['votes'])}")
    print(f"  divisions w/o a page   : {len(res['uncovered'])}  -> {unc.relative_to(ROOT)}")
    print(f"  wrote {out.relative_to(ROOT)} ({out.stat().st_size:,} bytes)")
    if res["problems"]:
        print(f"  problems ({len(res['problems'])}):")
        for p in res["problems"]:
            print("   -", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())

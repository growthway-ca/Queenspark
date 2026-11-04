"""Build the vote dataset for the 42nd, 43rd and 44th Parliaments.

For each parliament:
  1. Sessions from the house-documents index.
  2. Every bill page in each session (sponsor, status history, vote links).
  3. Every sitting day's Votes and Proceedings -> every recorded division,
     named by scraper.classify.
  4. 43rd Parliament onward: vote-search pages linked from bill pages give
     full rolls. Each is matched to its V&P division (same date and totals).
  5. Divisions with no vote-search page (all of the 42nd; motions later) get
     their roll from the V&P surname lists, matched against the roster for
     that parliament (scraper.members / scraper.matcher). Names that don't
     match exactly one sitting member go to data/unmatched.csv and are left
     out of the roll.

Outputs (data/):
  votes-p42.json, votes-p43.json, votes-p44.json   the vote dataset
  bills-p<N>.json         bill metadata used for the Downey and curated files
  meta-p<N>.json          per vote: printed totals, session, V&P source
  totals-only-p<N>.json   divisions printed with totals but no names
  unmatched.csv           V&P names that could not be matched
  downey-bills.json       bills Doug Downey sponsored
  curated.json            votes on Downey's bills, on bills naming a Simcoe
                          County municipality, and on bills/votes in tags.json

Usage:  python -m scraper.build [--offline] [--parliaments 42 43 44]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter

from .classify import classify
from .common import party_code, slug
from .fetch import BASE, ROOT, Fetcher
from .matcher import Matcher
from .members import build_roster, load_roster, save_roster
from .parse import (
    bill_links, parse_bill_page, parse_vote_page, parse_vp_divisions,
    sessions_from_house_docs_index, sitting_days,
)

DATA = ROOT / "data"
DOWNEY = "doug-downey"

# Simcoe County's sixteen lower-tier municipalities plus the separated cities
# of Barrie and Orillia, and the County itself. Names that are also ordinary
# words (Tiny, Tay, Essa, Severn, Midland, Clearview) only count in the form
# "Township/Town of X" or "X Township".
SIMCOE_RE = re.compile(
    r"\b(Barrie|Orillia|Collingwood|Innisfil|Bradford West Gwillimbury|Bradford|New Tecumseth|"
    r"Wasaga Beach|Penetanguishene|Oro-Medonte|Springwater|Adjala-Tosorontio|Ramara|"
    r"County of Simcoe|Simcoe County)\b"
    r"|\b(?:Township|Town) of (?:Tiny|Tay|Essa|Severn|Midland|Clearview)\b"
    r"|\b(?:Tiny|Tay|Essa|Severn|Clearview) Township\b"
)


def vp_url(p: int, s: int, day: str) -> str:
    return f"/en/legislative-business/house-documents/parliament-{p}/session-{s}/{day}/votes-proceedings"


def bill_nums_of(label: str) -> list[str]:
    """'Bill 76' -> ['76']; 'Time allocation (Bills 24, 10)' -> ['24', '10']."""
    if label.startswith("Bill "):
        return [label.split()[1].lower()]
    m = re.search(r"\(Bills?\s+([^)]+)\)", label)
    return [n.strip().lower() for n in m.group(1).split(",")] if m else []


class Build:
    def __init__(self, fetcher: Fetcher, refresh_rosters: bool):
        self.f = fetcher
        self.refresh_rosters = refresh_rosters
        self.problems: list[str] = []
        self.unmatched: list[dict] = []
        self.all_votes: list[tuple[int, int, dict]] = []   # (parliament, session, vote)
        self.all_bills: list[dict] = []

    # ------------------------------------------------------------------
    def roster(self, p: int):
        path = DATA / f"roster-p{p}.json"
        if path.exists() and not self.refresh_rosters:
            return load_roster(p)
        members = build_roster(p, self.f, self.problems)
        save_roster(p, members)
        return members

    def parliament(self, p: int) -> None:
        print(f"Parliament {p}", file=sys.stderr)
        index = self.f.get("/en/legislative-business/house-documents")
        sessions = [s for pp, s in sessions_from_house_docs_index(index) if pp == p]
        members = self.roster(p)
        matcher = Matcher(members, p)

        bills: dict[tuple[int, str], dict] = {}
        divisions = []     # dicts: session, div, bill, title, stage
        for s in sessions:
            listing = self.f.get(f"/en/legislative-business/bills/parliament-{p}/session-{s}")
            for href in bill_links(listing, p, s):
                html = self.f.get(href)
                if html is None:
                    self.problems.append(f"bill page 404: {href}")
                    continue
                b = parse_bill_page(html, BASE + href)
                b.update(parliament=p, session=s, long_title="")
                bills[(s, b["number"].lower())] = b
            titles = {num: b["title"] for (ss, num), b in bills.items() if ss == s}

            house = self.f.get(f"/en/legislative-business/house-documents/parliament-{p}/session-{s}")
            for day in sitting_days(house, p, s):
                html = self.f.get(vp_url(p, s, day))
                if html is None:
                    self.problems.append(f"no Votes and Proceedings page (yet): {BASE}{vp_url(p, s, day)}")
                    continue
                prev = None
                for d in parse_vp_divisions(html, day, BASE + vp_url(p, s, day)):
                    bill, title, stage, prev = classify(d, prev, titles)
                    divisions.append({"session": s, "div": d, "bill": bill, "title": title, "stage": stage})
                    if d.stage and d.bill_number and (s, d.bill_number.lower()) in bills and "," in d.subject:
                        bills[(s, d.bill_number.lower())]["long_title"] = d.subject.split(",", 1)[1].strip()

        for d in divisions:
            dv = d["div"]
            for note in dv.notes:
                self.problems.append(f"source irregularity {dv.source} #{dv.index}: {note}")
            if dv.ayes_total < 0 or dv.nays_total < 0:
                self.problems.append(f"V&P division without totals: {dv.source} #{dv.index}")
            elif dv.names_listed and (len(dv.ayes), len(dv.nays)) != (dv.ayes_total, dv.nays_total):
                self.problems.append(
                    f"V&P list/total mismatch {dv.source} #{dv.index}: lists {len(dv.ayes)}-{len(dv.nays)}, "
                    f"printed {dv.ayes_total}-{dv.nays_total}")

        # -- vote-search pages ------------------------------------------------
        page_for: dict[int, object] = {}
        if p >= 43:
            urls = sorted({v for b in bills.values() for v in b["votes"] if f"/parliament-{p}/" in v})
            for u in urls:
                html = self.f.get(u)
                if html is None:
                    self.problems.append(f"vote page 404: {u}")
                    continue
                page = parse_vote_page(html, BASE + u)
                for up in page.unknown_parties:
                    self.problems.append(f"unknown party on {page.url}: {up}")
                same = [d for d in divisions
                        if d["div"].date == page.date and id(d) not in page_for
                        and (d["div"].ayes_total, d["div"].nays_total) == (page.ayes_total, page.nays_total)]
                num = page.bill.split()[-1].lower()
                cands = [d for d in same if bill_nums_of(d["bill"]) == [num]] or (same if len(same) == 1 else [])
                if not cands:
                    self.problems.append(f"no V&P division matches vote page {page.url}")
                    continue
                page_for[id(cands[0])] = page

        # -- assemble votes ---------------------------------------------------
        votes, meta, totals_only = [], {}, []
        ids = Counter()
        disagreements = Counter()
        for d in divisions:
            dv = d["div"]
            page = page_for.get(id(d))
            if page is not None:
                bill, title, stage, source = page.bill, page.title, page.stage, page.url
                # Vote-search pages show each member's *current* party and
                # styling, so party and the minister flag come from the roster
                # on the vote date. Page values are kept only if the member
                # isn't in the roster (reported as a problem).
                roll = []
                for row, mslug in zip(page.roll, page.member_slugs):
                    row = list(row)
                    mem = matcher.by_slug.get(mslug)
                    party = mem.party_on(dv.date) if mem else None
                    if mem is None or party is None:
                        self.problems.append(f"{row[0]} ({mslug}) on {page.url} not in roster on {dv.date}; page values kept")
                    else:
                        if party_code(party) != row[1]:
                            disagreements["party"] += 1
                        if mem.minister_on(dv.date) != row[4]:
                            disagreements["hon"] += 1
                        row[1], row[2], row[4] = party_code(party), mem.riding_on(dv.date) or row[2], mem.minister_on(dv.date)
                    roll.append(row)
                unmatched_n = 0
            else:
                incomplete = dv.names_listed and (len(dv.ayes), len(dv.nays)) != (dv.ayes_total, dv.nays_total)
                if not dv.names_listed or incomplete:
                    totals_only.append({
                        "date": dv.date, "bill": d["bill"], "title": d["title"], "stage": d["stage"],
                        "ayes": dv.ayes_total, "nays": dv.nays_total, "result": dv.result_line,
                        "reason": (f"names list in source incomplete ({len(dv.ayes)} of {dv.ayes_total} Ayes, "
                                   f"{len(dv.nays)} of {dv.nays_total} Nays)") if incomplete
                                  else "source prints totals only",
                        "source": dv.source,
                    })
                    continue
                bill, title, stage, source = d["bill"], d["title"], d["stage"], dv.source
                roll, unmatched_n = [], 0
                base_id = f"{slug(bill)}-{slug(stage)}-{dv.date}"
                for side, names in (("y", dv.ayes), ("n", dv.nays)):
                    for token in names:
                        mem, cands = matcher.match(token, dv.date)
                        party = mem.party_on(dv.date) if mem else None
                        if mem is None or party is None:
                            unmatched_n += 1
                            self.unmatched.append({
                                "parliament": p, "date": dv.date, "vote": base_id, "side": side,
                                "name": token, "candidates": " ".join(cands), "source": dv.source,
                            })
                            continue
                        roll.append([mem.full_name, party_code(party), mem.riding_on(dv.date), side, mem.minister_on(dv.date)])

            base_id = f"{slug(bill)}-{slug(stage)}-{dv.date}"
            ids[base_id] += 1
            vid = base_id if ids[base_id] == 1 else f"{base_id}-{ids[base_id]}"
            votes.append({"id": vid, "bill": bill, "title": title, "stage": stage,
                          "date": dv.date, "source": source, "roll": roll})
            printed = [page.ayes_total, page.nays_total] if page is not None else [dv.ayes_total, dv.nays_total]
            meta[vid] = {"session": d["session"], "printed": printed, "vp_printed": [dv.ayes_total, dv.nays_total],
                         "unmatched": unmatched_n, "vp_source": dv.source, "vp_index": dv.index,
                         "from": "vote-search" if page is not None else "votes-proceedings"}
            self.all_votes.append((p, d["session"], votes[-1]))

        if p >= 43:
            print(f"  vote-search rows corrected to party/minister status on the vote date: "
                  f"{disagreements['party']} party, {disagreements['hon']} minister flag",
                  file=sys.stderr)
        self.all_bills.extend(bills.values())

        DATA.mkdir(exist_ok=True)
        dump(DATA / f"votes-p{p}.json", votes, compact=True)
        dump(DATA / f"meta-p{p}.json", meta)
        dump(DATA / f"totals-only-p{p}.json", totals_only)
        dump(DATA / f"bills-p{p}.json", sorted(bills.values(), key=lambda b: (b["session"], b["number"])))
        print(f"  {len(votes)} votes ({sum(m['from'] == 'vote-search' for m in meta.values())} from vote search), "
              f"{len(totals_only)} totals-only divisions", file=sys.stderr)

    # ------------------------------------------------------------------
    def downey_and_curated(self) -> None:
        downey = []
        for b in self.all_bills:
            if not any(sp["slug"] == DOWNEY for sp in b["sponsors"]):
                continue
            stages: dict[str, dict] = {}
            for r in b["status"]:
                st = stages.setdefault(r["stage"], {"stage": r["stage"], "first_date": r["date"], "last_date": r["date"], "events": []})
                st["last_date"] = max(st["last_date"], r["date"])
                st["events"].append({k: r[k] for k in ("date", "activity", "outcome", "committee")})
            first = stages.get("First Reading", {}).get("first_date")
            ra = stages.get("Royal Assent", {}).get("first_date")
            downey.append({
                "bill": b["bill"], "number": b["number"], "title": b["title"], "long_title": b["long_title"],
                "parliament": b["parliament"], "session": b["session"],
                "first_reading": first,
                "stages": [s for s in stages.values() if s["stage"] not in ("First Reading",)],
                "royal_assent": ra,
                "co_sponsors": [sp["name"] for sp in b["sponsors"] if sp["slug"] != DOWNEY],
                "source": b["source"],
            })
        downey.sort(key=lambda b: (b["parliament"], b["session"], b["first_reading"] or ""))
        dump(DATA / "downey-bills.json", downey)

        keys_downey = {(b["parliament"], b["session"], b["number"].lower()) for b in downey}
        keys_simcoe = {(b["parliament"], b["session"], b["number"].lower()) for b in self.all_bills
                       if SIMCOE_RE.search(b["title"]) or SIMCOE_RE.search(b["long_title"] or "")}
        tags = json.loads((DATA / "tags.json").read_text()) if (DATA / "tags.json").exists() else {}
        keys_tag = {(t["parliament"], t["session"], str(t["bill"]).lower().replace("bill ", ""))
                    for t in tags.get("bills", [])}
        tagged_ids = set(tags.get("votes", []))
        wanted = keys_downey | keys_simcoe | keys_tag
        curated = [v for p, s, v in self.all_votes
                   if v["id"] in tagged_ids or any((p, s, n) in wanted for n in bill_nums_of(v["bill"]))]
        curated.sort(key=lambda v: v["date"])
        dump(DATA / "curated.json", curated, compact=True)
        simcoe_titles = sorted(f"p{p}s{s} Bill {n}" for p, s, n in keys_simcoe)
        print(f"Downey bills: {len(downey)}; Simcoe bills: {simcoe_titles}; curated votes: {len(curated)}",
              file=sys.stderr)

    def write_unmatched(self) -> None:
        with (DATA / "unmatched.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, ["parliament", "date", "vote", "side", "name", "candidates", "source"])
            w.writeheader()
            w.writerows(self.unmatched)


def dump(path, obj, compact: bool = False) -> None:
    if compact:
        path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    else:
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parliaments", type=int, nargs="+", default=[42, 43, 44])
    ap.add_argument("--offline", action="store_true", help="use the cache only")
    ap.add_argument("--refresh-rosters", action="store_true", help="rebuild data/roster-p<N>.json")
    args = ap.parse_args(argv)

    b = Build(Fetcher(offline=args.offline), args.refresh_rosters)
    for p in args.parliaments:
        b.parliament(p)
    if set(args.parliaments) == {42, 43, 44}:
        b.downey_and_curated()
    b.write_unmatched()
    print(f"\nlive requests: {b.f.live_requests}; unmatched names: {len(b.unmatched)}")
    if b.problems:
        print(f"problems ({len(b.problems)}):")
        for pr in b.problems:
            print("  -", pr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

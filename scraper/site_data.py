"""Compact the dataset into site/data.json for the explorer (site/index.html).

Rolls are stored as indexes into a "seat" table (member, party, riding,
minister flag), which keeps the file small while preserving the party and
riding each member had on the day of each vote.

Usage:  python -m scraper.site_data
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict

from .fetch import ROOT
from .members import load_roster

DATA = ROOT / "data"
SITE = ROOT / "site"
PARLIAMENTS = (42, 43, 44)
PARTY_ORDER = ["PC", "NDP", "Liberal", "Green", "Ind"]


def kind_of(bill: str, stage: str) -> str:
    s = stage.lower()
    if "time allocation" in s:
        return "ta"
    if "closure" in s or bill.startswith("Closure"):
        return "closure"
    if bill.startswith("Opposition Day"):
        return "opp"
    if bill.startswith("Private Members' Motion"):
        return "pmm"
    if bill.startswith("Adjournment"):
        return "adj"
    if bill in ("Budget motion", "Address in Reply"):
        return "conf"
    if bill.startswith("Bill ") or bill.startswith("Motion for leave"):
        return "bill"
    return "motion"


def bill_nums(label: str) -> list[str]:
    if label.startswith("Bill "):
        return [label.split()[1].lower()]
    m = re.search(r"\(Bills?\s+([^)]+)\)", label)
    return [n.strip().lower() for n in m.group(1).split(",")] if m else []


def main() -> int:
    seats: dict[tuple, int] = {}
    seat_rows: list[list] = []
    people: dict[str, int] = {}
    ridings: dict[str, int] = {}

    def idx(table: dict, key) -> int:
        if key not in table:
            table[key] = len(table)
        return table[key]

    curated_ids = {v["id"] for v in json.loads((DATA / "curated.json").read_text())}
    downey_keys = {(b["parliament"], b["session"], b["number"].lower())
                   for b in json.loads((DATA / "downey-bills.json").read_text())}

    # Bills: who sponsored them and whether they are government bills.
    bills_out: dict[str, dict] = {}
    rosters = {p: {m.slug: m for m in load_roster(p)} for p in PARLIAMENTS}
    bill_meta: dict[tuple, dict] = {}
    for p in PARLIAMENTS:
        for b in json.loads((DATA / f"bills-p{p}.json").read_text()):
            bill_meta[(p, b["session"], b["number"].lower())] = b

    def bill_record(p: int, s: int, num: str) -> str | None:
        key = f"{p}-{s}-{num}"
        if key in bills_out:
            return key
        b = bill_meta.get((p, s, num))
        if b is None:
            return None
        first = next((r["date"] for r in b["status"] if r["stage"] == "First Reading"), None)
        ra = next((r["date"] for r in b["status"] if r["stage"] == "Royal Assent"), None)
        sponsors = []
        gov = False
        for sp in b["sponsors"]:
            mem = rosters[p].get(sp["slug"])
            party = None
            if mem and first:
                from .common import party_code
                pl = mem.party_on(first)
                party = party_code(pl) if pl else None
                gov = gov or bool(mem.minister_on(first))
            sponsors.append([sp["name"], party])
        btype = "Private bill" if num.startswith("pr") else ("Government bill" if gov else "Private member's bill")
        bills_out[key] = {
            "k": key, "n": b["bill"], "t": b["title"], "lt": b.get("long_title") or "",
            "p": p, "s": s, "sp": sponsors, "type": btype, "fr": first, "ra": ra,
            "url": b["source"], "downey": (p, s, num) in downey_keys,
        }
        return key

    votes_out = []
    for p in PARLIAMENTS:
        votes = json.loads((DATA / f"votes-p{p}.json").read_text())
        meta = json.loads((DATA / f"meta-p{p}.json").read_text())
        for v in votes:
            m = meta[v["id"]]
            y, n = [], []
            for name, party, riding, vote, hon in v["roll"]:
                key = (idx(people, name), PARTY_ORDER.index(party), idx(ridings, riding or ""), hon)
                if key not in seats:
                    seats[key] = len(seat_rows)
                    seat_rows.append(list(key))
                (y if vote == "y" else n).append(seats[key])
            bkeys = [k for k in (bill_record(p, m["session"], num) for num in bill_nums(v["bill"])) if k]
            flags = []
            if v["id"] in curated_ids:
                flags.append("local")
            if any(bills_out[k]["downey"] for k in bkeys):
                flags.append("downey")
            votes_out.append({
                "id": v["id"], "b": v["bill"], "t": v["title"], "st": v["stage"], "d": v["date"],
                "p": p, "s": m["session"], "src": v["source"], "k": kind_of(v["bill"], v["stage"]),
                "bk": bkeys, "f": flags, "y": y, "n": n,
            })
    votes_out.sort(key=lambda v: (v["d"], v["id"]))

    # Totals-only divisions, for completeness counts on the method page.
    totals_only = []
    for p in PARLIAMENTS:
        for t in json.loads((DATA / f"totals-only-p{p}.json").read_text()):
            totals_only.append({"d": t["date"], "b": t["bill"], "st": t["stage"], "a": t["ayes"],
                                "n": t["nays"], "why": t["reason"], "src": t["source"], "p": p})

    # Member profile facts from the rosters (party and riding history, cabinet roles).
    from .members import CABINET_RE
    profiles = {}
    for p in PARLIAMENTS:
        for mem in rosters[p].values():
            pr = profiles.setdefault(mem.full_name, {"slug": mem.slug, "parties": {}, "ridings": {}, "roles": {}})
            for iv in mem.parties:
                pr["parties"][(iv.value, iv.start)] = [iv.value, iv.start, iv.end]
            for iv in mem.ridings:
                pr["ridings"][(iv.value, iv.start)] = [iv.value, iv.start, iv.end]
            for iv in mem.roles:
                if CABINET_RE.match(iv.value) and iv.end >= "2018-06-07":
                    pr["roles"][(iv.value, iv.start)] = [iv.value, iv.start, iv.end]
    prof_out = {}
    for name in people:
        pr = profiles.get(name)
        if pr is None:
            continue
        prof_out[name] = {
            "slug": pr["slug"],
            "parties": sorted(pr["parties"].values(), key=lambda r: r[1]),
            "ridings": sorted(pr["ridings"].values(), key=lambda r: r[1]),
            "roles": sorted(pr["roles"].values(), key=lambda r: r[1]),
        }

    out = {
        "built": max(v["d"] for v in votes_out),
        "parties": PARTY_ORDER,
        "people": list(people),
        "ridings": list(ridings),
        "seats": seat_rows,
        "votes": votes_out,
        "bills": list(bills_out.values()),
        "profiles": prof_out,
        "totalsOnly": totals_only,
        "editorial": json.loads((DATA / "editorial.json").read_text()),
        "downeyBills": json.loads((DATA / "downey-bills.json").read_text()),
    }
    SITE.mkdir(exist_ok=True)
    path = SITE / "data.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    # Standalone copy for static hosting (e.g. Netlify): the artifact host adds
    # the document skeleton itself, a plain web server does not.
    dist = SITE / "dist"
    dist.mkdir(exist_ok=True)
    page = (SITE / "index.html").read_text(encoding="utf-8")
    (dist / "index.html").write_text(
        '<!doctype html>\n<html lang="en-CA">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        + page + "\n</html>\n", encoding="utf-8")
    (dist / "data.json").write_bytes(path.read_bytes())
    print(f"site/data.json: {len(votes_out)} votes, {len(people)} people, {len(seat_rows)} seats, "
          f"{len(bills_out)} bills, {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

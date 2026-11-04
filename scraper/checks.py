"""Acceptance checks from CLAUDE.md, plus a summary.

Usage:  python -m scraper.checks
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter

from .fetch import ROOT

DATA = ROOT / "data"


def load(parliament: int) -> list[dict] | None:
    path = DATA / f"votes-p{parliament}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def tally(vote: dict) -> tuple[int, int]:
    return (sum(r[3] == "y" for r in vote["roll"]), sum(r[3] == "n" for r in vote["roll"]))


def voted(vote: dict, name: str) -> str | None:
    for r in vote["roll"]:
        if r[0] == name:
            return r[3]
    return None


def check_bill76(failures: list[str]) -> None:
    votes = load(44)
    if votes is None:
        failures.append("Bill 76: data/votes-p44.json missing")
        return
    v = next((v for v in votes if v["id"] == "bill-76-third-reading-2025-12-09"), None)
    if v is None:
        failures.append("Bill 76: third reading of 2025-12-09 not found")
        return
    t = tally(v)
    ok = t == (70, 34) and voted(v, "Doug Downey") == "y" and voted(v, "Andrea Khanjin") == "y"
    print(f"[{'PASS' if ok else 'FAIL'}] Bill 76 third reading 2025-12-09: {t[0]} for, {t[1]} against; "
          f"Downey={voted(v, 'Doug Downey')}, Khanjin={voted(v, 'Andrea Khanjin')}")
    if not ok:
        failures.append("Bill 76 check")


def check_bill245(failures: list[str]) -> None:
    votes = load(42)
    meta_path = DATA / "meta-p42.json"
    if votes is None or not meta_path.exists():
        failures.append("Bill 245: data/votes-p42.json missing")
        return
    meta = json.loads(meta_path.read_text())
    v = next((v for v in votes if v["id"] == "bill-245-third-reading-2021-04-15"), None)
    if v is None:
        failures.append("Bill 245: third reading of 2021-04-15 not found")
        print("[FAIL] Bill 245 third reading 2021-04-15 not found")
        return
    t, m = tally(v), meta[v["id"]]
    ok = list(t) == m["printed"] and m["unmatched"] == 0
    print(f"[{'PASS' if ok else 'FAIL'}] Bill 245 third reading 2021-04-15: roll {t[0]}-{t[1]}, "
          f"V&P printed {m['printed'][0]}-{m['printed'][1]} ({m['vp_source']}); Downey={voted(v, 'Doug Downey')}")
    if not ok:
        failures.append("Bill 245 check")


def check_totals(failures: list[str]) -> None:
    """Roll counts (plus any unmatched names) equal the printed totals."""
    for p in (42, 43, 44):
        votes, mp = load(p), DATA / f"meta-p{p}.json"
        if votes is None or not mp.exists():
            continue
        meta = json.loads(mp.read_text())
        exact = with_unmatched = 0
        bad = []
        for v in votes:
            m = meta[v["id"]]
            t = tally(v)
            if list(t) == m["printed"]:
                exact += 1
            elif t[0] + t[1] + m["unmatched"] == sum(m["printed"]):
                with_unmatched += 1
            else:
                bad.append(f"{v['id']}: roll {t[0]}-{t[1]} (+{m['unmatched']} unmatched) vs printed {m['printed']}")
        print(f"[{'PASS' if not bad else 'FAIL'}] totals p{p}: {exact} exact, "
              f"{with_unmatched} short only by unmatched names, {len(bad)} wrong")
        for b in bad[:10]:
            print("    ", b)
        if bad:
            failures.append(f"totals p{p}")


def check_schema(failures: list[str]) -> None:
    parties = {"PC", "NDP", "Liberal", "Green", "Ind"}
    for p in (42, 43, 44):
        votes = load(p)
        if votes is None:
            continue
        ids = [v["id"] for v in votes]
        bad = []
        if len(ids) != len(set(ids)):
            bad.append("duplicate ids")
        for v in votes:
            for r in v["roll"]:
                if len(r) != 5 or r[1] not in parties or r[3] not in ("y", "n") or r[4] not in (0, 1):
                    bad.append(f"{v['id']}: bad roll row {r}")
                    break
        print(f"[{'PASS' if not bad else 'FAIL'}] schema p{p}: {len(votes)} votes")
        for b in bad[:10]:
            print("    ", b)
        if bad:
            failures.append(f"schema p{p}")


def summary() -> None:
    print("\nSummary")
    for p in (42, 43, 44):
        votes = load(p)
        path = DATA / f"votes-p{p}.json"
        if votes is None:
            print(f"  p{p}: not built yet")
            continue
        print(f"  p{p}: {len(votes)} votes, {path.stat().st_size:,} bytes")
        mp = DATA / f"meta-p{p}.json"
        if mp.exists():
            src = Counter(m["from"] for m in json.loads(mp.read_text()).values())
            print(f"        {src['vote-search']} from vote search, {src['votes-proceedings']} from Votes and Proceedings")
        to = DATA / f"totals-only-p{p}.json"
        if to.exists():
            print(f"        {len(json.loads(to.read_text()))} divisions printed with totals only (no names; not in the file)")
    um = DATA / "unmatched.csv"
    if um.exists():
        with um.open() as f:
            rows = list(csv.reader(f))[1:]
        print(f"  unmatched names: {len(rows)}")
    else:
        print("  unmatched names: 0 (no unmatched.csv)")
    for name in ("downey-bills.json", "curated.json"):
        f = DATA / name
        print(f"  {name}: {f'{f.stat().st_size:,} bytes' if f.exists() else 'not built yet'}")


def main() -> int:
    failures: list[str] = []
    check_bill76(failures)
    check_bill245(failures)
    check_totals(failures)
    check_schema(failures)
    summary()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

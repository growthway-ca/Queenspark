"""Acceptance checks from CLAUDE.md, plus a summary.

Usage:  python -m scraper.checks
"""

from __future__ import annotations

import csv
import json
import sys

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
        unc = DATA / f"uncovered-p{p}.json"
        if unc.exists():
            print(f"        {len(json.loads(unc.read_text()))} V&P divisions without a vote-search page")
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
    check_schema(failures)
    summary()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

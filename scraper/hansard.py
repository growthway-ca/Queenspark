"""Analyse the Legislative Assembly's structured Hansard (data resources page).

Files are downloaded to cache/hansard/ (see README). Each row is one
paragraph of the House record: date, order of business, subject, member ID,
person speaking, text.

For each member this counts, across 2018 to the latest file:
  - paragraphs and words spoken in the House
  - members' statements and petitions presented (the main ways a backbench
    or cabinet MPP raises a riding issue on their own initiative)
  - paragraphs that name a place in their riding

Usage:  python -m scraper.hansard
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

from .fetch import ROOT

HANSARD = ROOT / "cache" / "hansard"
DATA = ROOT / "data"
csv.field_size_limit(10**9)

# Place names that clearly belong to each riding. Only names that identify the
# riding are used, so a mention is a mention of the member's own community.
RIDINGS = {
    "Doug Downey": ("Barrie—Springwater—Oro-Medonte",
                    ["Barrie", "Springwater", "Oro-Medonte", "Midhurst", "Elmvale", "Minesing", "Snow Valley", "Phelpston", "Hillsdale", "Shanty Bay", "Craighurst", "Horseshoe Valley"]),
    "Andrea Khanjin": ("Barrie—Innisfil", ["Barrie", "Innisfil", "Alcona", "Stroud", "Cookstown", "Lefroy", "Sandy Cove"]),
    "Jill Dunlop": ("Simcoe North", ["Orillia", "Midland", "Penetanguishene", "Tay", "Tiny", "Severn", "Ramara", "Coldwater", "Victoria Harbour", "Port McNicoll", "Simcoe North"]),
    "Brian Saunderson": ("Simcoe—Grey", ["Collingwood", "Wasaga Beach", "Clearview", "Stayner", "New Tecumseth", "Alliston", "Essa", "Angus", "Blue Mountains", "Simcoe–Grey", "Simcoe—Grey"]),
    "Caroline Mulroney": ("York—Simcoe", ["Bradford", "East Gwillimbury", "Georgina", "Keswick", "Sutton", "York—Simcoe"]),
}


def rows():
    """Yield (date, order, subject, member_id, speaker, text) from every file."""
    for path in sorted(HANSARD.iterdir()):
        if path.suffix == ".csv":
            raw = path.read_bytes()
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("cp1252", errors="replace")
            reader = csv.reader(io.StringIO(text))
            header = next(reader)
            for r in reader:
                if len(r) >= 7:
                    yield _norm(r[1]), r[2], r[3], r[4], r[5], r[6]
        elif path.suffix == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True)
            ws = wb.worksheets[0]
            it = ws.iter_rows(values_only=True)
            next(it)
            for r in it:
                if r and len(r) >= 7:
                    yield _norm(r[1]), r[2] or "", r[3] or "", str(r[4] or ""), r[5] or "", r[6] or ""


def _norm(d) -> str:
    if isinstance(d, datetime):
        return d.date().isoformat()
    s = str(d or "").strip()
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y", "%m-%d-%Y %H:%M", "%m-%d-%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return s[:10]


def main() -> int:
    by_id_names: dict[str, Counter] = defaultdict(Counter)
    stats: dict[str, dict] = defaultdict(lambda: {"paras": 0, "words": 0, "statements": 0, "petitions": 0,
                                                 "questions_oq": 0, "mentions": 0, "days": set(),
                                                 "by_year": defaultdict(lambda: Counter())})
    orders = Counter()
    first = last = None
    for date, order, subject, mid, speaker, text in rows():
        if not mid or not mid.strip().isdigit():
            continue
        first = min(first or date, date)
        last = max(last or date, date)
        by_id_names[mid][speaker] += 1
        orders[order] += 1
        s = stats[mid]
        s["paras"] += 1
        s["words"] += len(str(text).split())
        s["days"].add(date)
        o = (order or "").lower()
        y = date[:4]
        s["by_year"][y]["paras"] += 1
        if o.startswith("members") and "statements" in o:
            s["statements"] += 1
            s["by_year"][y]["statements"] += 1
        if o.startswith("petitions"):
            s["petitions"] += 1
            s["by_year"][y]["petitions"] += 1
        s.setdefault("_text", []).append((date, o, str(text)))

    # Resolve member names: the most common speaker label, stripped of titles.
    def name_of(mid: str) -> str:
        label = by_id_names[mid].most_common(1)[0][0]
        m = re.search(r"\((?:Hon\.\s*)?([^)]+)\)", label)
        label = m.group(1) if m else label
        return re.sub(r"^(Hon\.|Mr\.|Ms\.|Mrs\.|Miss|M\.|Mme|MPP)\s*", "", label).rstrip(":").strip()

    names = {mid: name_of(mid) for mid in stats}
    out = {"period": [first, last], "orders": orders.most_common(40), "members": {}}
    for mid, s in stats.items():
        nm = names[mid]
        rec = {"id": mid, "name": nm, "paras": s["paras"], "words": s["words"], "days": len(s["days"]),
               "statements": s["statements"], "petitions": s["petitions"],
               "by_year": {y: dict(c) for y, c in sorted(s["by_year"].items())}}
        if nm in RIDINGS:
            riding, places = RIDINGS[nm]
            rx = re.compile(r"\b(" + "|".join(re.escape(p) for p in places) + r")\b")
            hits = [(d, o, t) for d, o, t in s["_text"] if rx.search(t)]
            rec["riding"] = riding
            rec["mentions"] = len(hits)
            rec["mention_days"] = len({d for d, _, _ in hits})
            rec["mentions_by_year"] = dict(Counter(d[:4] for d, _, _ in hits))
            rec["statement_mentions"] = sum(1 for d, o, t in hits if "statement" in o)
            rec["examples"] = [(d, o, t[:220]) for d, o, t in hits[-6:]]
        out["members"][mid] = rec
    DATA.mkdir(exist_ok=True)
    (DATA / "hansard-summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Hansard {first} to {last}: {sum(s['paras'] for s in stats.values()):,} member paragraphs, {len(stats)} members")
    return 0


if __name__ == "__main__":
    sys.exit(main())

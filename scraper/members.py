"""Per-parliament rosters from the members directory.

Sources (clean URLs only):
  /en/members/parliament-<n>      members who sat in that parliament
  /en/members/all/<slug>          riding representation, party affiliations
                                  and career roles, each with dates

A roster entry knows, for any date, the member's riding, party and whether
they held a cabinet post (styled "Hon."). Results are written to
data/roster-p<n>.json so they can be inspected.

Usage:  python -m scraper.members 42 43 44 [--offline]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field

from .common import clean, party_code
from .fetch import ROOT, Fetcher
from .parse import soup_of

DATA = ROOT / "data"
FAR_FUTURE = "9999-12-31"

# Career-role titles that make a member a minister of the Crown (styled "Hon.").
# Parliamentary assistants, critics, whips and committee posts do not count.
CABINET_RE = re.compile(
    r"^(Premier\b|Deputy Premier\b|Minister\b|Associate Minister\b|Attorney General\b|"
    r"Solicitor General\b|President of the Treasury Board\b|Chair of (the )?Cabinet\b)",
    re.I,
)


@dataclass
class Interval:
    value: str
    start: str
    end: str  # inclusive end date, FAR_FUTURE for "present"

    def covers(self, day: str) -> bool:
        return self.start <= day <= self.end


@dataclass
class Member:
    slug: str
    full_name: str        # "Doug Downey" (no "Hon.")
    surname: str          # "Downey"
    ridings: list[Interval] = field(default_factory=list)
    parties: list[Interval] = field(default_factory=list)
    roles: list[Interval] = field(default_factory=list)

    def _at(self, intervals: list[Interval], day: str) -> list[Interval]:
        return [i for i in intervals if i.covers(day)]

    def riding_on(self, day: str) -> str | None:
        hits = self._at(self.ridings, day)
        # On a changeover day two intervals touch; the later one wins.
        return max(hits, key=lambda i: i.start).value if hits else None

    def party_on(self, day: str) -> str | None:
        hits = self._at(self.parties, day)
        return max(hits, key=lambda i: i.start).value if hits else None

    def sitting_on(self, day: str) -> bool:
        return bool(self._at(self.ridings, day))

    def minister_on(self, day: str) -> int:
        """1 if the member held a cabinet post on that day.

        Role intervals are treated as half-open (end day excluded) so a
        minister shuffled out on day D is not counted as one on D.
        """
        for r in self.roles:
            if CABINET_RE.match(r.value) and r.start <= day and (day < r.end or r.end == FAR_FUTURE):
                return 1
        return 0


def _iso(t) -> str:
    return (t.get("datetime") or "")[:10] if t else ""


def _intervals_from_p(p, value: str) -> Interval | None:
    times = p.find_all("time")
    if not times:
        return None
    start = _iso(times[0])
    end = _iso(times[1]) if len(times) > 1 else FAR_FUTURE
    return Interval(value=value, start=start, end=end)


def _accordion(s, key: str):
    for btn in s.select("button.accordion-button"):
        if clean(btn.get_text()).lower().startswith(key):
            return s.select_one(btn.get("data-bs-target", ""))
    return None


def parse_member(html: str, slug: str, listed_name: str) -> Member:
    """`listed_name` is the directory form "Downey, Hon. Doug"."""
    surname, _, given = listed_name.partition(",")
    given = re.sub(r"^Hon\.?\s+", "", clean(given))
    m = Member(slug=slug, full_name=clean(f"{given} {surname}"), surname=clean(surname))
    s = soup_of(html)

    box = _accordion(s, "riding representation")
    if box is not None:
        riding = None
        for el in box.find_all(["h4", "div"]):
            if el.name == "h4":
                riding = clean(el.get_text())
            elif "views-row" in (el.get("class") or []) and riding:
                p = el.find("p")
                iv = _intervals_from_p(p, riding) if p else None
                if iv:
                    m.ridings.append(iv)

    box = _accordion(s, "party affiliation")
    if box is not None:
        for p in box.select(".views-row p"):
            label = clean(p.find(string=True))
            iv = _intervals_from_p(p, label)
            if iv:
                m.parties.append(iv)

    box = _accordion(s, "career details")
    if box is not None:
        for p in box.select(".views-row p"):
            label = clean(p.find(string=True))
            iv = _intervals_from_p(p, label)
            if iv:
                m.roles.append(iv)
    return m


def directory(html: str) -> list[tuple[str, str]]:
    """(slug, 'Surname, Given') pairs from a /en/members/parliament-<n> page."""
    out = []
    for a in soup_of(html).select('a[href^="/en/members/all/"]'):
        slug = a["href"].rsplit("/", 1)[-1]
        name = clean(a.get_text())
        if "," in name:
            out.append((slug, name))
    return sorted(set(out))


def build_roster(parliament: int, fetcher: Fetcher, problems: list[str]) -> list[Member]:
    listing = directory(fetcher.get(f"/en/members/parliament-{parliament}"))
    members = []
    for slug, name in listing:
        html = fetcher.get(f"/en/members/all/{slug}")
        if html is None:
            problems.append(f"member page 404: {slug}")
            continue
        mem = parse_member(html, slug, name)
        for iv in mem.parties:
            try:
                party_code(iv.value)
            except ValueError:
                problems.append(f"unknown party label {iv.value!r} for {slug}")
        if not mem.ridings:
            problems.append(f"no riding history parsed for {slug}")
        members.append(mem)
    return members


def save_roster(parliament: int, members: list[Member]) -> None:
    DATA.mkdir(exist_ok=True)
    path = DATA / f"roster-p{parliament}.json"
    path.write_text(json.dumps([asdict(m) for m in members], ensure_ascii=False, indent=1), encoding="utf-8")


def load_roster(parliament: int) -> list[Member]:
    raw = json.loads((DATA / f"roster-p{parliament}.json").read_text(encoding="utf-8"))
    out = []
    for r in raw:
        m = Member(slug=r["slug"], full_name=r["full_name"], surname=r["surname"])
        for k in ("ridings", "parties", "roles"):
            setattr(m, k, [Interval(**i) for i in r[k]])
        out.append(m)
    return out


def fold(text: str) -> str:
    """Case-, accent- and dash-insensitive key for names and ridings."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[‐-―−-]+", "-", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = text.replace("’", "'").replace("St. ", "St ")
    return re.sub(r"\s+", " ", text).strip().lower()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("parliaments", type=int, nargs="+")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args(argv)
    fetcher = Fetcher(offline=args.offline)
    for p in args.parliaments:
        problems: list[str] = []
        members = build_roster(p, fetcher, problems)
        save_roster(p, members)
        print(f"Parliament {p}: {len(members)} members -> data/roster-p{p}.json")
        for pr in problems:
            print("  -", pr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

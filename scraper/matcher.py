"""Match Votes and Proceedings surnames to roster members.

A V&P entry is either a surname ("Downey") or a surname with the riding
("Smith (Peterborough—Kawartha)"). A match must be unique among members
sitting on the vote date; otherwise the entry is reported, never guessed.

Members whose surname in the directory differs from the one printed in V&P
at the time (e.g. a later name change) are handled by scraper/aliases.json,
which maps a printed surname to a member slug for a given parliament. Every
alias there was checked by hand against the member's page.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .members import Member, fold

ALIASES = Path(__file__).with_name("aliases.json")
TOKEN_RE = re.compile(r"^(.*?)\s*\(([^)]+)\)\s*$")


def load_aliases(parliament: int) -> dict[str, str]:
    if not ALIASES.exists():
        return {}
    raw = json.loads(ALIASES.read_text(encoding="utf-8"))
    return {fold(k): v for k, v in raw.get(str(parliament), {}).items() if not k.startswith("_")}


class Matcher:
    def __init__(self, members: list[Member], parliament: int):
        self.members = members
        self.by_slug = {m.slug: m for m in members}
        self.aliases = load_aliases(parliament)

    def match(self, token: str, day: str) -> tuple[Member | None, list[str]]:
        """Return (member, candidate slugs). member is None unless unique."""
        m = TOKEN_RE.match(token)
        surname, riding = (m.group(1), m.group(2)) if m else (token, None)
        key = fold(surname)
        sitting = [x for x in self.members if x.sitting_on(day)]
        cands = [x for x in sitting if fold(x.surname) == key]
        if not cands and key in self.aliases:
            alias = self.by_slug.get(self.aliases[key])
            cands = [alias] if alias is not None and alias.sitting_on(day) else []
        if riding is not None:
            cands = [x for x in cands if fold(x.riding_on(day) or "") == fold(riding)]
        if len(cands) == 1:
            return cands[0], [cands[0].slug]
        return None, [x.slug for x in cands]

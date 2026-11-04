"""Shared helpers: party codes, text cleanup, stage names."""

from __future__ import annotations

import re
import unicodedata

PARTY_CODES = {
    "progressive conservative party of ontario": "PC",
    "progressive conservative": "PC",
    "new democratic party of ontario": "NDP",
    "new democratic party": "NDP",
    "ontario liberal party": "Liberal",
    "liberal": "Liberal",
    "green party of ontario": "Green",
    "green": "Green",
    "independent": "Ind",
    "independent member": "Ind",
}


class UnknownParty(ValueError):
    pass


def party_code(name: str) -> str:
    key = clean(name).lower()
    if key in PARTY_CODES:
        return PARTY_CODES[key]
    raise UnknownParty(name)


def clean(text: str | None) -> str:
    if text is None:
        return ""
    text = unicodedata.normalize("NFC", text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


HON_RE = re.compile(r"^Hon\.?\s+")


def split_hon(name: str) -> tuple[str, int]:
    """'Hon. Doug Downey' -> ('Doug Downey', 1)."""
    name = clean(name)
    if HON_RE.match(name):
        return HON_RE.sub("", name), 1
    return name, 0


def stage_from_heading(heading: str) -> str:
    """'Vote on third reading' -> 'Third reading'."""
    h = clean(heading)
    h = re.sub(r"^Vote on\s+", "", h, flags=re.I)
    return h[:1].upper() + h[1:] if h else h


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

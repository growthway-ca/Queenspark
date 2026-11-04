"""Parsers for vote-search pages, bill pages, V&P pages and index pages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from .common import clean, party_code, split_hon, stage_from_heading

BILL_RE = re.compile(r"^(Bill\s+(?:Pr)?\d+)\s*,\s*(.+)$", re.I)


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ---------------------------------------------------------------------------
# Index pages
# ---------------------------------------------------------------------------

def sessions_from_house_docs_index(html: str) -> list[tuple[int, int]]:
    found = set(re.findall(r"house-documents/parliament-(\d+)/session-(\d+)", html))
    return sorted((int(p), int(s)) for p, s in found)


def sitting_days(html: str, parliament: int, session: int) -> list[str]:
    rx = rf"house-documents/parliament-{parliament}/session-{session}/(\d{{4}}-\d\d-\d\d)/"
    return sorted(set(re.findall(rx, html)))


def bill_links(html: str, parliament: int, session: int) -> list[str]:
    rx = rf'href="(/en/legislative-business/bills/parliament-{parliament}/session-{session}/bill-[a-z0-9]+)"'
    return sorted(set(re.findall(rx, html)))


def vote_links(html: str) -> list[str]:
    rx = r'href="(?:https://www\.ola\.org)?(/en/legislative-business/votes-search/parliament-\d+/session-\d+/[^"?#]+)"'
    return sorted(set(re.findall(rx, html)))


# ---------------------------------------------------------------------------
# Vote-search detail page
# ---------------------------------------------------------------------------

@dataclass
class VotePage:
    url: str
    heading: str            # h1
    stage: str              # "Third reading"
    date: str               # YYYY-MM-DD
    parliament_session: str  # "44th Parliament, 1st Session"
    outcome: str
    ayes_total: int
    nays_total: int
    bill_url: str | None
    roll: list[list] = field(default_factory=list)   # [name, party, riding, y/n, hon]
    member_slugs: list[str] = field(default_factory=list)
    unknown_parties: list[str] = field(default_factory=list)

    @property
    def bill(self) -> str:
        m = BILL_RE.match(self.heading)
        return clean(m.group(1)) if m else self.heading

    @property
    def title(self) -> str:
        m = BILL_RE.match(self.heading)
        return clean(m.group(2)) if m else self.heading


def _text(el: Tag | None) -> str:
    return clean(el.get_text(" ")) if el else ""


def parse_vote_page(html: str, url: str) -> VotePage:
    s = soup_of(html)
    header = s.select_one(".votes-detail-header-container")
    if header is None:
        raise ValueError(f"no vote header on {url}")
    date_el = header.select_one("time.divisions-meta-date")
    bill_a = header.select_one('a[href*="/legislative-business/bills/"]')
    page = VotePage(
        url=url,
        heading=_text(header.select_one("h1")),
        stage=stage_from_heading(_text(header.select_one(".votes-meta h2"))),
        date=(date_el.get("datetime") or "")[:10] if date_el else "",
        parliament_session=_text(header.select_one(".vote-meta-parliament")),
        outcome=_text(s.select_one(".divisions-outcome-result")),
        ayes_total=int(_text(s.select_one(".division-count_aye")) or -1),
        nays_total=int(_text(s.select_one(".division-count_nay")) or -1),
        bill_url=bill_a["href"] if bill_a else None,
    )
    for row in s.select("tr.listCardView"):
        name_a = row.select_one(".list-card-name a")
        raw_name = _text(row.select_one(".list-card-name"))
        name, hon = split_hon(raw_name)
        party_raw = _text(row.select_one(".list-card-party"))
        try:
            party = party_code(party_raw)
        except ValueError:
            page.unknown_parties.append(f"{raw_name}: {party_raw!r}")
            continue
        vote_raw = _text(row.select_one(".list-card-vote")).lower()
        vote = {"aye": "y", "for": "y", "nay": "n", "against": "n"}.get(vote_raw)
        if vote is None:
            raise ValueError(f"unknown vote value {vote_raw!r} on {url}")
        page.roll.append([name, party, _text(row.select_one(".list-card-riding")), vote, hon])
        page.member_slugs.append(name_a["href"].rsplit("/", 1)[-1] if name_a else "")
    return page


# ---------------------------------------------------------------------------
# Votes and Proceedings: recorded divisions
# ---------------------------------------------------------------------------

DIVISION_RE = re.compile(r"\b(Carried|Lost|Agreed to|Negatived)\b.*\bon the following division", re.I)
COUNT_RE = re.compile(r"\((\d+)\)")
VP_BILL_RE = re.compile(r"\bBill\s+((?:Pr)?\d+)\b", re.I)
VP_STAGE_RE = re.compile(r"\b(First|Second|Third) Reading of", re.I)


@dataclass
class VPDivision:
    date: str
    index: int               # 1-based order within the day
    context: str             # English text of the motion/question
    result_line: str         # "Carried on the following division:"
    ayes: list[str]
    nays: list[str]
    ayes_total: int
    nays_total: int
    source: str

    @property
    def bill_number(self) -> str | None:
        m = VP_BILL_RE.search(self.context)
        return m.group(1) if m else None

    @property
    def stage(self) -> str | None:
        m = VP_STAGE_RE.search(self.context)
        return f"{m.group(1).capitalize()} reading" if m else None


def parse_vp_divisions(html: str, date: str, source: str) -> list[VPDivision]:
    s = soup_of(html)
    root = s.select_one("main") or s
    # Walk English paragraphs and division headers/lists in document order.
    items: list[tuple[str, Tag]] = []
    for el in root.find_all(["p", "h4", "h5", "table"]):
        if el.name == "h5" and "divisionHeader" in (el.get("class") or []):
            items.append(("count", el))
        elif el.name == "table" and "votesList" in (el.get("class") or []):
            items.append(("list", el))
        elif el.name in ("p", "h4"):
            td = el.find_parent("td")
            if td is not None and td.get("lang") == "en":
                items.append(("text", el))

    divisions: list[VPDivision] = []
    recent: list[str] = []          # English paragraphs since the last division
    current: VPDivision | None = None
    side: str | None = None
    for kind, el in items:
        if kind == "text":
            txt = _text(el)
            if DIVISION_RE.search(txt):
                current = VPDivision(
                    date=date, index=len(divisions) + 1,
                    context=" ".join(recent[-3:]), result_line=txt,
                    ayes=[], nays=[], ayes_total=-1, nays_total=-1, source=source,
                )
                divisions.append(current)
                recent = []
                side = None
            else:
                recent.append(txt)
                if el.name == "h4":
                    recent = [txt]
        elif kind == "count" and current is not None:
            en = _text(el.find("span", attrs={"lang": "en"})) or _text(el)
            m = COUNT_RE.search(_text(el))
            n = int(m.group(1)) if m else -1
            side = "ayes" if en.lower().startswith("aye") else "nays" if en.lower().startswith("nay") else None
            if side == "ayes":
                current.ayes_total = n
            elif side == "nays":
                current.nays_total = n
        elif kind == "list" and current is not None and side is not None:
            names = [_text(d) for d in el.find_all("div", attrs={"lang": "en"})]
            getattr(current, side).extend(n for n in names if n)
            side = None
    return divisions

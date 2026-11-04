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
INLINE_TOTALS_RE = re.compile(r"Ayes\s+(\d+)\s*,\s*Nays\s+(\d+)", re.I)
COUNT_RE = re.compile(r"\((\d+)\)")
VP_BILL_RE = re.compile(r"\bBill\s+((?:Pr)?\d+)\b", re.I)
VP_STAGE_RE = re.compile(r"\b(First|Second|Third) Reading of", re.I)
# Paragraphs that open a new item of business (as opposed to "Debate arose...").
SUBJECT_RE = re.compile(
    r"Reading of|^Motion\b|Notice of Motion|\bmoved\b|allocation of time|closure|"
    r"Address in Reply|question on the adoption|Budgetary Policy|Opposition Day",
    re.I,
)
DIV_HEADER_CLASSES = {"divisionHeader", "votesProceedingsDocdivisionHeader"}


@dataclass
class VPDivision:
    date: str
    index: int               # 1-based order within the day
    paragraphs: list[str]    # English paragraphs since the previous division
    result_line: str         # "Carried on the following division:"
    ayes: list[str]
    nays: list[str]
    ayes_total: int
    nays_total: int
    source: str
    names_listed: bool = True  # False for "... division – Ayes 44, Nays 73" (totals only)

    @property
    def subject(self) -> str:
        """The paragraph that introduced the question being voted on."""
        for p in self.paragraphs:
            if SUBJECT_RE.search(p):
                return p
        return self.paragraphs[0] if self.paragraphs else ""

    @property
    def context(self) -> str:
        return " ".join(self.paragraphs)

    @property
    def bill_number(self) -> str | None:
        m = VP_BILL_RE.search(self.subject)
        return m.group(1) if m else None

    @property
    def stage(self) -> str | None:
        m = VP_STAGE_RE.search(self.subject)
        return f"{m.group(1).capitalize()} reading" if m else None


def _vp_names(header: Tag) -> list[str]:
    """Surnames listed in the table that follows a division header."""
    table = header.find_next("table")
    if table is None:
        return []
    els = table.find_all("div", attrs={"lang": "en"}) or [
        p for p in table.find_all("p") if "MsoFooter" in (p.get("class") or [])
    ]
    return [n for n in (_text(e) for e in els) if n]


def parse_vp_divisions(html: str, date: str, source: str) -> list[VPDivision]:
    """Recorded divisions on one Votes and Proceedings page.

    Handles both layouts ola.org uses (h5.divisionHeader + table.votesList,
    and the Word-derived p.votesProceedingsDocdivisionHeader + p.MsoFooter),
    plus the one-line "on the following division – Ayes N, Nays M" form.
    """
    s = soup_of(html)
    root = s.select_one("main") or s
    divisions: list[VPDivision] = []
    recent: list[str] = []
    current: VPDivision | None = None
    for el in root.find_all(["p", "h4", "h5"]):
        classes = set(el.get("class") or [])
        if classes & DIV_HEADER_CLASSES:
            if current is None:
                continue
            en = _text(el.find("span", attrs={"lang": re.compile("^en$", re.I)})) or _text(el)
            m = COUNT_RE.search(_text(el))
            n = int(m.group(1)) if m else -1
            names = _vp_names(el)
            if en.lower().startswith("aye"):
                current.ayes_total, current.ayes = n, names
            elif en.lower().startswith("nay"):
                current.nays_total, current.nays = n, names
            continue
        td = el.find_parent("td")
        if td is None or td.get("lang") != "en":
            continue
        if el.name == "p" and el.find_parent("p") is not None:
            continue
        txt = _text(el)
        if not txt:
            continue
        if DIVISION_RE.search(txt):
            current = VPDivision(
                date=date, index=len(divisions) + 1, paragraphs=recent[-8:],
                result_line=txt, ayes=[], nays=[], ayes_total=-1, nays_total=-1,
                source=source,
            )
            m = INLINE_TOTALS_RE.search(txt)
            if m:
                current.ayes_total, current.nays_total = int(m.group(1)), int(m.group(2))
                current.names_listed = False
            divisions.append(current)
            recent = []
        elif el.name == "h4":
            recent = [txt]
        else:
            recent.append(txt)
    return divisions

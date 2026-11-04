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

DIVISION_RE = re.compile(r"\bon the following division\b", re.I)
INLINE_TOTALS_RE = re.compile(r"Ayes\s+(\d+)\s*,\s*Nays\s+(\d+)", re.I)
INLINE_TOTALS_REV_RE = re.compile(r"[-–]\s*(\d+)\s+Ayes\s*,\s*(\d+)\s+Nays", re.I)  # "– 0 Ayes, 0 Nays" (tie)
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
    notes: list[str] = field(default_factory=list)  # source irregularities handled

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
    if not els and "votesList" in (table.get("class") or []):
        els = table.find_all("td")      # 2022-23 pages: bare names in cells
    return [n for n in (_text(e) for e in els) if n]


NAME_CELL_CLASSES = {"MsoFooter", "votesProceedingsDocdocHide"} | DIV_HEADER_CLASSES


def _is_english_text(el: Tag) -> bool:
    """English paragraph of the running record (not a name cell, not French)."""
    td = el.find_parent("td")
    if td is None:
        return False
    if td.get("lang") == "en":
        return True
    if td.get("lang"):
        return False
    # Word-derived layout: English-only text sits in a one-cell row.
    if set(el.get("class") or []) & NAME_CELL_CLASSES:
        return False
    tr = td.find_parent("tr")
    if tr is None or len(tr.find_all("td", recursive=False)) != 1:
        return False
    if el.find(attrs={"lang": re.compile("^fr", re.I)}) is not None:
        return False
    return True


def parse_vp_divisions(html: str, date: str, source: str) -> list[VPDivision]:
    """Recorded divisions on one Votes and Proceedings page.

    Handles both layouts ola.org uses (h5.divisionHeader + table.votesList,
    and the Word-derived p.votesProceedingsDocdivisionHeader + p.MsoFooter),
    plus the one-line "on the following division – Ayes N, Nays M" form.
    """
    if "divisionHeader" not in html and re.search(r"AYES\s*/\s*POUR", html, re.I):
        return parse_vp_divisions_legacy(html, date, source)
    s = soup_of(html)
    root = s.select_one("main") or s
    divisions: list[VPDivision] = []
    recent: list[str] = []
    current: VPDivision | None = None
    for el in root.find_all(["p", "h4", "h5", "div"]):
        classes = set(el.get("class") or [])
        if el.name == "div":
            # English-only blocks (motion texts, some subjects) sit in
            # td[colspan=2] > div.datawrapper; skip the ones holding a division list.
            if "datawrapper" not in classes or el.find(["h5", "table", "p"]) is not None:
                continue
            txt = _text(el)
            if txt:
                recent.append(txt)
            continue
        if classes & DIV_HEADER_CLASSES:
            if current is None:
                continue
            en = _text(el.find("span", attrs={"lang": re.compile("^en$", re.I)})) or _text(el)
            m = COUNT_RE.search(_text(el))
            n = int(m.group(1)) if m else -1
            names = _vp_names(el) if n != 0 else []
            if en.lower().startswith("aye"):
                current.ayes_total, current.ayes = n, names
            elif en.lower().startswith("nay"):
                current.nays_total, current.nays = n, names
            continue
        if not _is_english_text(el):
            continue
        if el.name == "p" and el.find_parent("p") is not None:
            continue
        txt = _text(el)
        if not txt:
            continue
        if DIVISION_RE.search(txt):
            current = VPDivision(
                date=date, index=len(divisions) + 1, paragraphs=recent[-40:],
                result_line=txt, ayes=[], nays=[], ayes_total=-1, nays_total=-1,
                source=source,
            )
            m = INLINE_TOTALS_RE.search(txt) or INLINE_TOTALS_REV_RE.search(txt)
            if m:
                current.ayes_total, current.nays_total = int(m.group(1)), int(m.group(2))
                current.names_listed = False
            divisions.append(current)
            recent = []
        elif current is not None and LEGACY_INLINE_RE.match(txt):
            im = LEGACY_INLINE_RE.match(txt)
            current.ayes_total, current.nays_total = int(im.group(1)), int(im.group(2))
            current.names_listed = False
        elif el.name == "h4":
            recent = [txt]
        else:
            recent.append(txt)
    return divisions


# ---------------------------------------------------------------------------
# Bill pages
# ---------------------------------------------------------------------------

def _us_date(text: str) -> str | None:
    from datetime import datetime
    try:
        return datetime.strptime(clean(text), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def parse_bill_page(html: str, url: str) -> dict:
    """Number, title, sponsors and status history of one bill."""
    s = soup_of(html)
    h1 = _text(s.select_one("h1.bill-title") or s.select_one("h1"))
    m = BILL_RE.match(h1)
    sponsors = []
    for a in s.select('.bill-sponsors a[href*="/members/all/"]'):
        name = _text(a)
        surname, _, given = name.partition(",")
        given = re.sub(r"^Hon\.?\s+", "", clean(given))
        sponsors.append({"slug": a["href"].rsplit("/", 1)[-1], "name": clean(f"{given} {surname}")})
    status = []
    for tr in s.select("table tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 4:
            continue
        day = _us_date(_text(tds[0]))
        if not day:
            continue
        status.append({
            "date": day, "stage": _text(tds[1]), "activity": _text(tds[2]),
            "outcome": _text(tds[3]), "committee": _text(tds[4]) if len(tds) > 4 else "",
        })
    status.sort(key=lambda r: r["date"])
    return {
        "bill": clean(m.group(1)) if m else h1,
        "number": (clean(m.group(1)).split()[-1] if m else ""),
        "title": clean(m.group(2)) if m else h1,
        "sponsors": sponsors,
        "status": status,
        "votes": vote_links(html),
        "source": url,
    }


# Legacy layout (42nd Parliament): one big table. English text sits in the
# first of two colspan=2 cells, English-only text in a colspan=4 cell,
# division headers read "AYES / POUR - 66" (or "... - Continued") and names
# are <p>s in plain cells.
LEGACY_INLINE_RE = re.compile(r"^AYES\s*[-–]\s*(\d+)\s+NAYS\s*[-–]\s*(\d+)", re.I)
NAME_LIKE_RE = re.compile(r"^[A-ZÀ-Ý][\w'’.\-]*(?: [A-ZÀ-Ý][\w'’\-]*)?(?:\s*\([^)]{2,60}\))?$")
LEGACY_HEADER_RE = re.compile(r"^(AYES|NAYS)\s*/\s*\w+\s*[-–]\s*(\d+|Continued)\b", re.I)


def _cell_texts(td) -> list[str]:
    ps = td.find_all(["p", "h4", "h5", "h6"])
    if ps:
        return [t for t in (_text(p) for p in ps) if t]
    return [t for t in (clean(x) for x in td.get_text("\n").split("\n")) if t]


def parse_vp_divisions_legacy(html: str, date: str, source: str) -> list[VPDivision]:
    """The 42nd Parliament layout: rows of one big table whose column spans
    vary from page to page. A row whose first cell spans 2+ columns is text
    (English in the first cell); a row whose first cell spans one column is a
    list of names, belonging to the most recent AYES/NAYS header."""
    s = soup_of(html)
    divisions: list[VPDivision] = []
    recent: list[str] = []
    current: VPDivision | None = None
    side: str | None = None
    for tr in s.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue
        unlabelled = (current is not None and side is None and not current.ayes and not current.nays
                      and current.ayes_total < 0 and current.names_listed)
        if current is not None and (side is not None or unlabelled):
            cells = [_cell_texts(td) for td in tds]
            flat = [t for c in cells for t in c]
            if unlabelled and flat and all(NAME_LIKE_RE.match(t) and any(c.islower() for c in t) for t in flat):
                # Source omits the "AYES / POUR - n" header; V&P always lists Ayes first.
                side = "ayes"
                current.notes.append("Ayes header missing in source; first name list taken as Ayes")
            if flat and side is not None and all(NAME_LIKE_RE.match(t) and not t.endswith((".", ":")) and any(c.islower() for c in t)
                            for t in flat):
                getattr(current, side).extend(flat)
                continue
        english = next((td for td in tds if _text(td)), tds[0])
        for txt in _cell_texts(english):
            im = LEGACY_INLINE_RE.match(txt)
            if im and current is not None:
                current.ayes_total, current.nays_total = int(im.group(1)), int(im.group(2))
                current.names_listed = False
                side = None
                continue
            hm = LEGACY_HEADER_RE.match(txt)
            if hm:
                if current is not None:
                    side = "ayes" if hm.group(1).upper() == "AYES" else "nays"
                    if hm.group(2).isdigit():
                        setattr(current, f"{side}_total", int(hm.group(2)))
                continue
            side = None
            if DIVISION_RE.search(txt):
                current = VPDivision(
                    date=date, index=len(divisions) + 1, paragraphs=recent[-40:],
                    result_line=txt, ayes=[], nays=[], ayes_total=-1, nays_total=-1, source=source,
                )
                m = INLINE_TOTALS_RE.search(txt) or INLINE_TOTALS_REV_RE.search(txt)
                if m:
                    current.ayes_total, current.nays_total = int(m.group(1)), int(m.group(2))
                    current.names_listed = False
                divisions.append(current)
                recent = []
            else:
                recent.append(txt)
    for d in divisions:
        for side in ("ayes", "nays"):
            names, total = getattr(d, side), getattr(d, f"{side}_total")
            uniq = list(dict.fromkeys(names))
            if len(uniq) < len(names) and (len(uniq) == total or total < 0):
                setattr(d, side, uniq)
                d.notes.append(f"{side} list printed with duplicates in source; de-duplicated")
            if total < 0 and d.names_listed and names:
                setattr(d, f"{side}_total", len(getattr(d, side)))
                d.notes.append(f"{side} total not printed; taken as the number of names listed")
    return divisions

"""Name a Votes and Proceedings division: bill (or motion short name), title, stage.

The V&P record before each division is a run of English paragraphs. The
item being voted on is introduced by a heading paragraph ("Third Reading of
Bill 5", "Opposition Day", "Private Members' Notice of Motion No. 4",
"Motion for allocation of time on Bill 60", ...). We take the *last* such
heading before the division; if there is none (a second division on the same
item), the previous division's item is carried forward.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .parse import VPDivision

READING_RE = re.compile(r"^(First|Second|Third) Reading of Bill\s+((?:Pr)?\d+)", re.I)
BILL_NUM_RE = re.compile(r"\bBill\s+((?:Pr)?\d+)\b", re.I)
MOVED_RE = re.compile(r"^(?:Hon\.\s+)?[A-Z][\w.'’ -]+?(?:\s*\([^)]*\))?\s+moved\b", re.U)

LEAVE_RE = re.compile(r"moved,? that leave be given to introduce a Bill entitled (.+?) and that the same be now read the first time", re.I)
AMEND_HEADING_RE = re.compile(r"^Amendment to the (amendment to the )?(.+)$", re.I)

ON_MOTION_FIRST_RE = re.compile(r"^On motion by .*?\bBill\s+((?:Pr)?\d+),\s*(.*?),? was introduced and read the first time", re.I)

HEADINGS = [
    ("leave", LEAVE_RE),
    ("first_on_motion", ON_MOTION_FIRST_RE),
    ("reading", READING_RE),
    ("time_allocation", re.compile(r"^Motion for allocation of time\b", re.I)),
    ("closure", re.compile(r"^Motion for closure\b", re.I)),
    ("budget", re.compile(r"Budgetary Policy of the Government", re.I)),
    ("address", re.compile(r"Address in Reply", re.I)),
    ("opposition_day", re.compile(r"^Opposition Day\b", re.I)),
    ("pmm", re.compile(r"Private Members[’'] Notice of Motion No\.?\s*(\d+)", re.I)),
    ("report", re.compile(r"(?:question|put) on the adoption of the (?:committee )?report", re.I)),
    ("adjourn_house", re.compile(r"\bmoved adjournment of the House\b", re.I)),
    ("adjourn_debate", re.compile(r"\bmoved adjournment of the debate\b", re.I)),
    ("motion_heading", re.compile(r"^(Motions|Government Motions?)$", re.I)),
    ("moved", MOVED_RE),
]
AMENDMENT_RE = re.compile(r"moved,? that the (?:motion|amendment)\b.*\bamended|moved that the amendment", re.I)
END_OF_TEXT_RE = re.compile(r"^(Debate arose|Debate resumed|The question was|Pursuant to|After some time|Il s)", re.I)


@dataclass
class Item:
    kind: str
    paragraphs: list[str]
    heading: str
    amendment: bool = False

    def bills(self) -> list[str]:
        nums = []
        for p in self.paragraphs[:2]:
            for n in BILL_NUM_RE.findall(p):
                if n not in nums:
                    nums.append(n)
        return nums


def _find_item(paragraphs: list[str]) -> Item | None:
    heads: list[tuple[int, str]] = []
    amended: set[int] = set()
    for i, p in enumerate(paragraphs):
        am = AMEND_HEADING_RE.match(p)
        if am:
            # "Amendment to the motion for allocation of time on Bill 5":
            # classify the motion being amended, flag it as an amendment.
            rest = am.group(2)
            rest = rest[:1].upper() + rest[1:]
            for kind, rx in HEADINGS:
                if kind != "moved" and rx.search(rest):
                    heads.append((i, kind))
                    amended.add(i)
                    paragraphs = paragraphs[:i] + [rest] + paragraphs[i + 1:]
                    break
            else:
                heads.append((i, "amendment_heading"))
            continue
        for kind, rx in HEADINGS:
            if rx.search(p):
                heads.append((i, kind))
                break
    if not heads:
        return None
    i, kind = heads[-1]
    # "X moved," directly after a heading belongs to that heading's item.
    if kind == "moved" and len(heads) > 1 and heads[-2][0] == i - 1:
        pi, pkind = heads[-2]
        if pkind != "motion_heading" and pkind != "moved":
            i, kind = pi, pkind
    if kind == "motion_heading":
        if i + 1 >= len(paragraphs):
            return None
        i, kind = i + 1, "moved"
    if kind == "amendment_heading":
        kind = "moved" if i + 1 < len(paragraphs) and MOVED_RE.match(paragraphs[i + 1]) else "other"
        return Item(kind=kind, paragraphs=paragraphs[i + 1:] if kind == "moved" else paragraphs[i:],
                    heading=paragraphs[i + 1] if kind == "moved" else paragraphs[i], amendment=True)
    return Item(kind=kind, paragraphs=paragraphs[i:], heading=paragraphs[i], amendment=i in amended)


def _motion_text(item: Item) -> str:
    """Mover plus the operative text of a motion."""
    mover = ""
    text: list[str] = []
    started = False
    for p in item.paragraphs:
        if not started:
            m = re.match(r"^(.*?)\s+moved\b", p)
            if m and MOVED_RE.match(p):
                mover = m.group(1)
                rest = p[m.end():].strip(" ,:")
                if rest:
                    text.append(rest)
                started = True
            continue
        if END_OF_TEXT_RE.match(p):
            break
        text.append(p)
    operative = [t for t in text if re.match(r"^(Therefore|That)\b", t)]
    body = " ".join(operative or text)
    return f"{mover}: {body}" if mover and body else (body or mover)


def classify(div: VPDivision, prev: Item | None, bill_titles: dict[str, str]) -> tuple[str, str, str, Item | None]:
    """Return (bill, title, stage, item) for one division."""
    paras = list(div.paragraphs)
    if len(div.result_line) > 80:      # 42nd: motion and result in one paragraph
        paras.append(div.result_line)
    item = _find_item(paras) or prev
    # After an adjournment division the House returns to the item it interrupted.
    carry = item
    if item is not None and item.kind in ("adjourn_house", "adjourn_debate"):
        idx = paras.index(item.heading) if item.heading in paras else 0
        carry = _find_item(paras[:idx]) or (prev if prev is not item else None)
    if item is None:
        return "Motion", (div.paragraphs[-1] if div.paragraphs else div.result_line), "Motion", None

    trailing = " ".join(paras)
    amendment = (item.amendment or bool(AMENDMENT_RE.search(trailing))) and item.kind not in ("adjourn_house", "adjourn_debate")

    def short(num: str) -> str:
        return bill_titles.get(num.lower(), "")

    k = item.kind
    if k == "leave":
        m = LEAVE_RE.search(item.heading)
        mover = re.match(r"^(.*?)\s+moved\b", item.heading)
        bill = "Motion for leave to introduce a bill"
        title = m.group(1) + (f" (moved by {mover.group(1)})" if mover else "")
        stage = "First reading"
    elif k == "first_on_motion":
        m = ON_MOTION_FIRST_RE.search(item.heading)
        bill, title, stage = f"Bill {m.group(1)}", short(m.group(1)) or m.group(2), "First reading"
    elif k == "reading":
        m = READING_RE.search(item.heading)
        num = m.group(2)
        long_title = item.heading.split(",", 1)[1].strip() if "," in item.heading else ""
        bill, title, stage = f"Bill {num}", short(num) or long_title, f"{m.group(1).capitalize()} reading"
    elif k == "time_allocation":
        nums = item.bills()
        bill = "Time allocation" + (f" (Bill{'s' if len(nums) > 1 else ''} {', '.join(nums)})" if nums else "")
        title = "; ".join(f"Bill {n}, {short(n)}" if short(n) else f"Bill {n}" for n in nums) or item.heading
        stage = "Time allocation motion"
    elif k == "closure":
        nums = item.bills()
        bill, title, stage = "Closure" + (f" (Bill {nums[0]})" if nums else ""), item.heading, "Closure motion"
    elif k == "budget":
        bill, title, stage = "Budget motion", item.heading, "Motion"
    elif k == "address":
        bill, title, stage = "Address in Reply", item.heading, "Motion"
    elif k == "opposition_day":
        num = re.search(r"No\.?\s*(\d+)", item.heading)
        bill = "Opposition Day motion" + (f" No. {num.group(1)}" if num else "")
        bill, title, stage = bill, _motion_text(item) or item.heading, "Motion"
    elif k == "pmm":
        num = dict(HEADINGS)["pmm"].search(item.heading).group(1)
        bill, title, stage = f"Private Members' Motion No. {num}", _motion_text(item) or item.heading, "Motion"
    elif k == "report":
        idx = paras.index(item.heading) if item.heading in paras else len(paras)
        nums = [n for p in paras[:idx + 1] for n in BILL_NUM_RE.findall(p)]
        if nums:
            bill, title = f"Bill {nums[-1]}", short(nums[-1]) or item.heading
        else:
            bill, title = "Committee report", item.heading
        stage = "Adoption of committee report"
    elif k == "adjourn_house":
        bill, title, stage = "Adjournment of the House", item.heading, "Adjournment motion"
    elif k == "adjourn_debate":
        bill, title, stage = "Adjournment of the debate", item.heading, "Adjournment motion"
    elif k == "other":
        bill, title, stage = "Motion", item.heading, "Motion"
    else:  # "moved": a government or other motion introduced by its mover
        title = _motion_text(item)
        body = title.split(": ", 1)[-1]
        if re.search(r"That this question be now put", body, re.I):
            bill, stage = "Closure", "Closure motion"
        elif re.match(r"that the House do now adjourn", body, re.I):
            bill, stage = "Adjournment of the House", "Adjournment motion"
        else:
            bill, stage = "Government motion" if item.heading.startswith("Hon.") else "Motion", "Motion"
            if re.match(r"That the (?:motion|amendment) be amended", body, re.I):
                amendment = True

    if amendment:
        stage = f"Amendment ({stage[0].lower() + stage[1:]})"
    return bill, title, stage, carry

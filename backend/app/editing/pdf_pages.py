"""Page-level changes to a PDF: delete a page, move a page, turn a page, or add a
line of text to a page. The pages that stay are copied as they are, so their
text, pictures and layout are untouched.

The model gets each page's first line and replies with operations (see
PAGE_SYSTEM_PROMPT). Page numbers always mean the page's number in the file
as it was uploaded, so the operations can be applied in order and any of them
can be rejected later in the review (changes.py rebuilds from the original).
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from app.editing.docx_ops import Change
from app.editing.layout import ParagraphLLM, chat_json

PAGE_OPS = ("delete_page", "move_page", "rotate_page", "add_text")
MAX_OPERATIONS = 40
MAX_TEXT = 300
MARGIN = 36  # points from the top or bottom edge for added text
TEXT_SIZE = 10

PAGE_SYSTEM_PROMPT = (
    "You change the pages of a PDF. You get a request and a numbered list of the pages, each with its first line. "
    'Reply with JSON only: {"operations": [ ... ]}\n'
    "Operations (\"page\" is a page number from the list):\n"
    '  {"op": "delete_page", "page": N}   remove page N\n'
    '  {"op": "move_page", "page": N, "after": M}   move page N so it comes right after page M (0 = to the front)\n'
    '  {"op": "rotate_page", "page": N, "degrees": 90}   turn page N (90, 180, 270 or -90)\n'
    '  {"op": "add_text", "page": N, "text": "...", "where": "top|bottom"}   a short line added to page N '
    "(a header or footer line, a note, a page label)\n"
    "Rules:\n"
    "- Do exactly what the request says and nothing else. Page numbers are the ones listed.\n"
    "- Do not invent text. A line added to a page must come from the request.\n"
    '- If the request cannot be done with these operations, reply {"operations": [], "problem": "one sentence saying why"}.'
)


@dataclass
class PageOp:
    kind: str
    page: int
    after: int = 0
    degrees: int = 0
    text: str = ""
    where: str = "bottom"


def _page_number(value, count: int, low: int = 1) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= count


def parse_page_ops(data: dict, page_count: int) -> tuple[list[PageOp], list[str]]:
    """The valid operations in the model's reply, and a sentence for each one dropped."""
    ops: list[PageOp] = []
    dropped: list[str] = []
    for raw in (data.get("operations") or [])[:MAX_OPERATIONS]:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("op", "")).strip().lower()
        page = raw.get("page")
        if kind not in PAGE_OPS:
            dropped.append(f"unknown operation “{raw.get('op')}”")
            continue
        if not _page_number(page, page_count):
            dropped.append(f"there is no page {page}")
            continue
        if kind == "delete_page":
            ops.append(PageOp(kind, page))
        elif kind == "move_page":
            after = raw.get("after", 0)
            if not _page_number(after, page_count, low=0) or after == page:
                dropped.append(f"page {page}: it can only move after another page (0 for the front)")
                continue
            ops.append(PageOp(kind, page, after=after))
        elif kind == "rotate_page":
            degrees = raw.get("degrees")
            if degrees not in (90, 180, 270, -90):
                dropped.append(f"page {page}: it can only turn by 90, 180 or 270 degrees")
                continue
            ops.append(PageOp(kind, page, degrees=degrees % 360))
        else:  # add_text
            text = " ".join(raw["text"].split())[:MAX_TEXT] if isinstance(raw.get("text"), str) else ""
            if not text:
                dropped.append(f"page {page}: no text given")
                continue
            where = raw.get("where") if raw.get("where") in ("top", "bottom") else "bottom"
            ops.append(PageOp(kind, page, text=text, where=where))
    return ops, dropped


def _label(op: PageOp) -> str:
    if op.kind == "delete_page":
        return f"page {op.page} deleted"
    if op.kind == "move_page":
        place = "to the front" if op.after == 0 else f"after page {op.after}"
        return f"page {op.page} moved {place}"
    if op.kind == "rotate_page":
        return f"page {op.page} turned {op.degrees}°"
    return f"text added to page {op.page} at the {op.where}"


def apply_page_ops(doc: pymupdf.Document, ops: list[PageOp]) -> tuple[list[Change], list[str]]:
    """Make the changes to `doc` (opened from the original file). Returns the change
    list and what could not be done."""
    order = list(range(len(doc)))  # original page indexes, in their new order
    turned: dict[int, int] = {}
    texts: dict[int, list[tuple[str, str]]] = {}
    changes: list[Change] = []
    skipped: list[str] = []

    for op_index, op in enumerate(ops):
        index = op.page - 1
        if index not in order:
            skipped.append(f"page {op.page} was already removed")
            continue
        if op.kind == "delete_page":
            if len(order) == 1:
                skipped.append("the last page can't be removed")
                continue
            order.remove(index)
            turned.pop(index, None)
            texts.pop(index, None)
        elif op.kind == "move_page":
            if op.after and (op.after - 1) not in order:
                skipped.append(f"page {op.after} was already removed, so page {op.page} stayed where it was")
                continue
            order.remove(index)
            if op.after == 0:
                order.insert(0, index)
            else:
                order.insert(order.index(op.after - 1) + 1, index)
        elif op.kind == "rotate_page":
            turned[index] = (turned.get(index, 0) + op.degrees) % 360
        else:
            texts.setdefault(index, []).append((op.text, op.where))
        changes.append(Change(op.kind, op.page, "", "", _label(op), op_index))

    doc.select(order)
    for new_index, original in enumerate(order):
        page = doc[new_index]
        if turned.get(original):
            page.set_rotation((page.rotation + turned[original]) % 360)
        for text, where in texts.get(original, []):
            y = MARGIN if where == "top" else page.rect.height - MARGIN
            page.insert_text(pymupdf.Point(MARGIN, y), text, fontsize=TEXT_SIZE, fontname="helv")
    return changes, skipped


def page_listing(doc: pymupdf.Document) -> str:
    """Each page's number and first line of text, for the model."""
    lines = []
    for number, page in enumerate(doc, start=1):
        first = next((line.strip() for line in page.get_text("text").splitlines() if line.strip()), "")
        lines.append(f"[{number}] {first[:80] if first else '(no text)'}")
    return "\n".join(lines)


def plan_page_ops(doc: pymupdf.Document, instruction: str, llm: ParagraphLLM) -> tuple[list[PageOp], str]:
    """Ask the model for page operations. Returns them and the model's reason when it has none."""
    data = chat_json(llm, PAGE_SYSTEM_PROMPT, f"Request:\n{instruction}\n\nPages:\n{page_listing(doc)}", temperature=0.1)
    ops, dropped = parse_page_ops(data, len(doc))
    problem = data.get("problem") if isinstance(data.get("problem"), str) else ""
    return ops, problem or (dropped[0] if dropped else "")


def summarize_pages(changes: list[Change]) -> str:
    return "; ".join(c.label for c in changes)


def rebuild_pages(base, dst, ops: list[PageOp]) -> list[str]:
    """Write `dst` from the original `base` with only `ops` applied. Returns what was skipped."""
    doc = pymupdf.open(str(base))
    try:
        _, skipped = apply_page_ops(doc, ops)
        doc.save(str(dst), garbage=4, deflate=True)
    finally:
        doc.close()
    return skipped

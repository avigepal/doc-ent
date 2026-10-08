"""Edit a PDF in place: the page, its graphics, tables, images and the style of
the text around the change all stay; only the words that change are taken
out and written again, in the same place, size and colour.

Two operations (see layout.py for when each is used):
  * replace -- find the words on each line and write them again where they
    were, keeping each piece's own bold/colour/size, and moving the rest of
    the line along if the new words are a different width;
  * reword  -- the model rewrites text blocks (paragraphs, headings, table
    cells); each changed block is cleared and written again in its own
    rectangle, shrinking the type a little if the new text is longer.

The new text is drawn in the PDF's own font when that font has the letters
needed (a PDF only keeps the letters it used, so often it does), otherwise in
the closest installed font.

Honest limits, which fall back to building a clean document instead: no text
layer (a scan), a script that needs shaping (Hindi, Arabic...), and text that
can't be fitted. Paragraphs are not re-flowed: a much longer replacement is set
smaller rather than pushing the lines below it down.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import pymupdf

from app.editing.engine import EditProgress
from app.editing.layout import (
    MAX_PARAGRAPHS,
    LayoutResult,
    LayoutUnsupported,
    ParagraphLLM,
    batches,
    reword_batch,
)
from app.editing.literal import find_spans, looks_like_name, parse_replacement
from app.editing.textspans import apply_spans

_FONT_DIRS = (
    Path("/usr/share/fonts/truetype/noto"),
    Path("/usr/share/fonts/truetype/crosextra"),
    Path("/usr/share/fonts/truetype/dejavu"),
)

# family -> (bold, italic) -> file name
_FILES = {
    "notosans": {(False, False): "NotoSans-Regular.ttf", (True, False): "NotoSans-Bold.ttf",
                 (False, True): "NotoSans-Italic.ttf", (True, True): "NotoSans-BoldItalic.ttf"},
    "notoserif": {(False, False): "NotoSerif-Regular.ttf", (True, False): "NotoSerif-Bold.ttf",
                  (False, True): "NotoSerif-Italic.ttf", (True, True): "NotoSerif-BoldItalic.ttf"},
    "notomono": {(False, False): "NotoSansMono-Regular.ttf", (True, False): "NotoSansMono-Bold.ttf",
                 (False, True): "NotoSansMono-Regular.ttf", (True, True): "NotoSansMono-Bold.ttf"},
    "carlito": {(False, False): "Carlito-Regular.ttf", (True, False): "Carlito-Bold.ttf",
                (False, True): "Carlito-Italic.ttf", (True, True): "Carlito-BoldItalic.ttf"},
    "dejavu": {(False, False): "DejaVuSans.ttf", (True, False): "DejaVuSans-Bold.ttf",
               (False, True): "DejaVuSans.ttf", (True, True): "DejaVuSans-Bold.ttf"},
}

# built-in PDF fonts (metric-compatible with Arial / Times / Courier)
_BUILTIN = {
    ("sans", False, False): "helv", ("sans", True, False): "hebo",
    ("sans", False, True): "heit", ("sans", True, True): "hebi",
    ("serif", False, False): "tiro", ("serif", True, False): "tibo",
    ("serif", False, True): "tiit", ("serif", True, True): "tibi",
    ("mono", False, False): "cour", ("mono", True, False): "cobo",
    ("mono", False, True): "coit", ("mono", True, True): "cobi",
}

_MIN_SCALE = 0.62  # never set text smaller than this fraction of its original size
_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


def _hex_color(value: int) -> tuple[float, float, float]:
    return ((value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255)


def _clean_name(name: str) -> str:
    return _SUBSET_PREFIX.sub("", name).lower().replace("-", "").replace(" ", "").replace("_", "")


def _style(span: dict) -> tuple[str, bool, bool]:
    """(family, bold, italic). The font's own name is the reliable hint:
    the "serif" flag is set on sans fonts by some producers."""
    flags, name = span.get("flags", 0), _clean_name(span.get("font", ""))
    bold = bool(flags & 16) or any(w in name for w in ("bold", "black", "heavy", "semibold"))
    italic = bool(flags & 2) or any(w in name for w in ("italic", "oblique"))
    serif_words = ("serif", "times", "georgia", "garamond", "palatino", "cambria", "minion", "caladea", "bookantiqua")
    if any(w in name for w in ("mono", "courier", "consolas", "menlo", "lucidaconsole")):
        family = "mono"
    elif any(w in name for w in serif_words) and "sans" not in name:
        family = "serif"
    else:
        family = "sans"
    return family, bold, italic


def _named_family(name: str) -> str | None:
    """An installed family that is (or is metric-compatible with) the font."""
    if "noto" in name:
        if "mono" in name:
            return "notomono"
        return "notoserif" if "serif" in name and "sans" not in name else "notosans"
    if "carlito" in name or "calibri" in name:
        return "carlito"
    if "dejavu" in name:
        return "dejavu"
    return None


def _needs_shaping(text: str) -> bool:
    """Scripts whose letters change shape or join (Indic, Arabic, Hebrew, Thai,
    CJK...) can't be drawn glyph by glyph here."""
    return any(0x0590 <= ord(c) < 0x2000 or ord(c) >= 0x2C00 for c in text)


def _covers(font: pymupdf.Font, text: str) -> bool:
    return all(c in "\t\r\n" or font.has_glyph(ord(c)) for c in text)


@dataclass
class _Choice:
    name: str
    measure: Callable[[str, float], float]
    source: dict | None = None  # how to load the font onto a page (None for the built-in fonts)


class _FontBook:
    """Loads fonts once per document."""

    def __init__(self, doc: pymupdf.Document):
        self.doc = doc
        self._installed: dict[str, tuple[Path, pymupdf.Font] | None] = {}
        self._embedded: dict[int, tuple[pymupdf.Font, bytes] | None] = {}

    def installed(self, family: str, style: tuple[str, bool, bool]) -> tuple[Path, pymupdf.Font] | None:
        file_name = _FILES[family][(style[1], style[2])]
        if file_name not in self._installed:
            found = next((d / file_name for d in _FONT_DIRS if (d / file_name).exists()), None)
            self._installed[file_name] = (found, pymupdf.Font(fontfile=str(found))) if found else None
        return self._installed[file_name]

    def embedded(self, xref: int) -> tuple[pymupdf.Font, bytes] | None:
        if xref not in self._embedded:
            result = None
            try:
                _name, ext, _type, buffer = self.doc.extract_font(xref)
                if buffer and ext in ("ttf", "otf"):
                    result = (pymupdf.Font(fontbuffer=buffer), buffer)
            except Exception:
                result = None
            self._embedded[xref] = result
        return self._embedded[xref]


class _PageFonts:
    """The fonts of one page: picks one for a piece of text and registers it
    on the page the first time it is used."""

    def __init__(self, book: _FontBook, page: pymupdf.Page):
        self.book = book
        self.page = page
        self.registered: set[str] = set()
        # fonts the page already uses, by name, so the original font can be reused
        self.xrefs = {_clean_name(f[3]): f[0] for f in page.get_fonts(full=True)}

    def ensure(self, choice: _Choice) -> str:
        """Load the font onto the page (once) and return its name. Done only
        after the old text is cleared: clearing rewrites the page's resources."""
        if choice.source and choice.name not in self.registered:
            self.page.insert_font(fontname=choice.name, **choice.source)
            self.registered.add(choice.name)
        return choice.name

    def _from_installed(self, family: str, style: tuple[str, bool, bool], text: str) -> _Choice | None:
        installed = self.book.installed(family, style)
        if installed is None or not _covers(installed[1], text):
            return None
        path, font = installed
        name = "sys" + re.sub(r"\W", "", path.stem.lower())
        return _Choice(name, lambda s, size, f=font: f.text_length(s, fontsize=size), {"fontfile": str(path)})

    def choose(self, span: dict, text: str) -> _Choice:
        style = _style(span)
        original = _clean_name(span.get("font", ""))

        # 1. the PDF's own font, when it has every letter needed
        xref = self.xrefs.get(original)
        embedded = self.book.embedded(xref) if xref else None
        if embedded and _covers(embedded[0], text):
            return _Choice(
                f"emb{xref}", lambda s, size, f=embedded[0]: f.text_length(s, fontsize=size), {"fontbuffer": embedded[1]}
            )

        # 2. the installed font with the same name (or a metric-compatible one)
        family = _named_family(original)
        if family:
            choice = self._from_installed(family, style, text)
            if choice:
                return choice

        # 3. the built-in Helvetica / Times / Courier family, for plain Latin text
        if all(ord(c) < 256 for c in text):
            name = _BUILTIN[style]
            return _Choice(name, lambda s, size, n=name: pymupdf.get_text_length(s, fontname=n, fontsize=size))

        # 4. an installed font that has the letters (curly quotes, bullets, rupee sign, Greek, Cyrillic...)
        preferred = {"serif": "notoserif", "mono": "notomono", "sans": "notosans"}[style[0]]
        for candidate in (preferred, "notosans", "dejavu"):
            choice = self._from_installed(candidate, style, text)
            if choice:
                return choice
        raise LayoutUnsupported("the fonts needed for this text aren't available")


def _clear(page: pymupdf.Page, rects: list[pymupdf.Rect]) -> None:
    """Remove the text under these rectangles, leaving lines, images and
    backgrounds as they are."""
    for rect in rects:
        page.add_redact_annot(rect, fill=False)
    page.apply_redactions(
        images=pymupdf.PDF_REDACT_IMAGE_NONE,
        graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
    )


def _span_rect(span: dict) -> pymupdf.Rect:
    """Just the glyph area of a span, so neighbouring lines aren't touched."""
    x0, _, x1, _ = span["bbox"]
    size, baseline = span["size"], span["origin"][1]
    return pymupdf.Rect(x0 + 0.1, baseline - size * 0.75, x1 - 0.1, baseline + size * 0.2)


def _text_blocks(page: pymupdf.Page) -> list[dict]:
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    return [b for b in data["blocks"] if b["type"] == 0 and b.get("lines")]


def _is_centred(block: dict, page: pymupdf.Page) -> bool:
    """A centred title or paragraph: the lines share a centre that is the
    page's centre. (A full-width left-aligned line also has the page's
    centre, so a single line must be clearly narrower than the page and a
    paragraph's lines must differ in width.)"""
    lines = block["lines"]
    centres = [(line["bbox"][0] + line["bbox"][2]) / 2 for line in lines]
    widths = [line["bbox"][2] - line["bbox"][0] for line in lines]
    page_centre = page.rect.x0 + page.rect.width / 2
    if abs(sum(centres) / len(centres) - page_centre) > page.rect.width * 0.02:
        return False
    if len(lines) == 1:
        return widths[0] < page.rect.width * 0.7
    return max(centres) - min(centres) < 2.0 and max(widths) - min(widths) > 3.0


# ---------- replace ----------

@dataclass
class _Group:
    """Spans of one line that sit together (a big gap starts a new group:
    a table column, a page number at the right edge)."""

    spans: list[dict]
    texts: list[str]
    limit: float  # the x the group may grow to
    centred: bool


def _groups(spans: list[dict]) -> list[list[int]]:
    groups: list[list[int]] = [[0]]
    for i in range(1, len(spans)):
        gap = spans[i]["bbox"][0] - spans[i - 1]["bbox"][2]
        if gap > spans[i]["size"] * 2.5:
            groups.append([])
        groups[-1].append(i)
    return groups


def _replace_on_page(page: pymupdf.Page, replacement, book: _FontBook) -> int:
    """Rewrite the lines that contain the old text. Returns how many places changed."""
    jobs: list[_Group] = []
    count = 0
    for block in _text_blocks(page):
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"]]
            if not spans:
                continue
            old = [s["text"] for s in spans]
            found = find_spans("".join(old), replacement)
            if not found:
                continue
            count += len(found)
            new = apply_spans(old, found)
            groups = _groups(spans)
            for g, indexes in enumerate(groups):
                if all(old[i] == new[i] for i in indexes):
                    continue
                last = g == len(groups) - 1
                if last:
                    limit = max(block["bbox"][2], spans[indexes[-1]]["bbox"][2])
                else:
                    limit = spans[groups[g + 1][0]]["bbox"][0] - 1
                centred = len(groups) == 1 and _is_centred(block, page)
                jobs.append(_Group([spans[i] for i in indexes], [new[i] for i in indexes], limit, centred))
    if not jobs:
        return 0

    if any(_needs_shaping(t) for job in jobs for t in job.texts):
        raise LayoutUnsupported("the new text uses a script that can't be set in place")

    fonts = _PageFonts(book, page)
    placed = []  # (text, choice, x, baseline, size, color): worked out before anything is cleared
    for job in jobs:
        choices = [fonts.choose(s, t) for s, t in zip(job.spans, job.texts)]
        widths = [c.measure(t, s["size"]) for c, s, t in zip(choices, job.spans, job.texts)]
        gaps = [job.spans[i + 1]["bbox"][0] - job.spans[i]["bbox"][2] for i in range(len(job.spans) - 1)] + [0.0]
        total = sum(widths) + sum(gaps)
        x = job.spans[0]["bbox"][0]
        scale = 1.0
        if total > job.limit - x > 0:
            scale = max((job.limit - x) / total, _MIN_SCALE)
        if job.centred:
            first, last_x = job.spans[0]["bbox"][0], job.spans[-1]["bbox"][2]
            x = (first + last_x) / 2 - total * scale / 2
        for span, text, choice, width, gap in zip(job.spans, job.texts, choices, widths, gaps):
            placed.append((text, choice, x, span["origin"][1], span["size"] * scale, _hex_color(span["color"])))
            x += (width + gap) * scale

    _clear(page, [_span_rect(s) for job in jobs for s in job.spans])
    for text, choice, x, baseline, size, color in placed:
        if text.strip():
            page.insert_text(pymupdf.Point(x, baseline), text, fontname=fonts.ensure(choice), fontsize=size, color=color)
    return count


# ---------- reword ----------

def _unit(lines: list[dict], limit_x: float | None = None) -> dict:
    """A piece of text edited as one: a paragraph, a list item, a table cell."""
    x0 = min(line["bbox"][0] for line in lines)
    y0 = min(line["bbox"][1] for line in lines)
    x1 = max(line["bbox"][2] for line in lines)
    y1 = max(line["bbox"][3] for line in lines)
    return {"type": 0, "lines": lines, "bbox": (x0, y0, x1, y1), "limit_x": limit_x}


_MARKERS = set("\u2022\u00b7\u25aa\u25cf\u25a0\u2013\u2014-*\u25e6\u2023")


def _line_size(line: dict) -> float:
    return line["spans"][0]["size"]


def _is_marker(line: dict) -> bool:
    """A list bullet or number sitting on its own."""
    text = "".join(s["text"] for s in line["spans"]).strip()
    return bool(text) and (all(c in _MARKERS for c in text) or bool(re.fullmatch(r"\(?\d{1,3}[.)]|[a-zA-Z][.)]", text)))


def _paragraphs(lines: list[dict], page_width: float) -> list[list[dict]]:
    """Group single lines into paragraphs by how they sit on the page: the
    same left edge and type size, one line directly under the other, and the
    line above running to the edge (a short line ends a paragraph, and a
    bullet or number starts one). The PDF's own blocks only serve as a hint:
    text written back by an earlier edit is a block of its own, so lines of
    different blocks must also reach the widest text on the page."""
    long_lines = [l for l in lines if len("".join(s["text"] for s in l["spans"])) >= 30]
    page_right = max((l["bbox"][2] for l in long_lines), default=None)
    markers = [line for line in lines if _is_marker(line)]
    lines = sorted((line for line in lines if not _is_marker(line)), key=lambda l: (l["bbox"][1], l["bbox"][0]))

    def starts_item(line: dict) -> bool:
        text = "".join(s["text"] for s in line["spans"]).lstrip()
        if text[:1] in _MARKERS or re.match(r"\d{1,3}[.)]\s", text):
            return True
        baseline = line["spans"][0]["origin"][1]
        return any(
            abs(m["spans"][0]["origin"][1] - baseline) < 2.5 and m["bbox"][2] <= line["bbox"][0] + 1 for m in markers
        )

    paragraphs: list[list[dict]] = []
    for line in lines:
        x0, y0, x1, _ = line["bbox"]
        size = _line_size(line)
        target = None
        if not starts_item(line):
            for paragraph in reversed(paragraphs):
                last = paragraph[-1]
                right = max(l["bbox"][2] for l in paragraph + [line])
                left = min(l["bbox"][0] for l in paragraph + [line])
                gap = y0 - last["bbox"][3]
                if last.get("block_id") == line.get("block_id"):
                    runs_to_edge = last["bbox"][2] >= right - 0.2 * (right - left)
                else:
                    runs_to_edge = page_right is not None and last["bbox"][2] >= page_right - 0.12 * page_width
                if (
                    abs(x0 - last["bbox"][0]) <= 3
                    and abs(size - _line_size(last)) <= 0.6
                    and -0.5 * size <= gap <= 0.45 * size
                    and runs_to_edge
                ):
                    target = paragraph
                    break
        if target is None:
            paragraphs.append([line])
        else:
            target.append(line)
    return paragraphs


def _text_units(page: pymupdf.Page) -> list[dict]:
    """The page's text split the way a person reads it (a paragraph, a list
    item, a table cell), in reading order."""
    units: list[dict] = []
    plain: list[dict] = []
    for block_id, block in enumerate(_text_blocks(page)):
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            groups = _groups(spans)
            if len(groups) == 1:
                plain.append({**line, "spans": spans, "block_id": block_id})
                continue
            # cells: a wide gap means several columns side by side on one line
            for g, indexes in enumerate(groups):
                cell = [spans[i] for i in indexes]
                bbox = (cell[0]["bbox"][0], min(s["bbox"][1] for s in cell), cell[-1]["bbox"][2], max(s["bbox"][3] for s in cell))
                if g + 1 < len(groups):
                    limit = spans[groups[g + 1][0]]["bbox"][0] - 4
                else:
                    limit = min(page.rect.x1 - 30, bbox[2] + (bbox[2] - bbox[0]) * 0.15)
                units.append(_unit([{"bbox": bbox, "spans": cell}], limit))

    for paragraph in _paragraphs(plain, page.rect.width):
        units.append(_unit(paragraph))
    units.sort(key=lambda u: (round(u["bbox"][1]), u["bbox"][0]))
    return units


def _block_text(block: dict) -> str:
    lines = ("".join(s["text"] for s in line["spans"]).strip() for line in block["lines"])
    return " ".join(line for line in lines if line)


def _first_span(block: dict) -> dict:
    for line in block["lines"]:
        for span in line["spans"]:
            if span["text"].strip():
                return span
    return block["lines"][0]["spans"][0]


def _line_pitch(block: dict) -> float | None:
    ys = [line["spans"][0]["origin"][1] for line in block["lines"] if line["spans"]]
    return ys[1] - ys[0] if len(ys) >= 2 else None


def _write_block(page: pymupdf.Page, fonts: _PageFonts, block: dict, text: str) -> None:
    """Write `text` into the block's rectangle, a little smaller if it is longer."""
    span = _first_span(block)
    choice = fonts.choose(span, text)
    name = fonts.ensure(choice)
    size0, color = span["size"], _hex_color(span["color"])
    # a little room below: a paragraph is followed by a gap, and French is longer than English
    rect = pymupdf.Rect(block["bbox"][0], block["bbox"][1], block["bbox"][2], block["bbox"][3] + size0 * 0.8)

    # One line stays one line, on its own baseline (cells, headings, list items):
    # same as a replacement, shrunk a little if the new text is wider.
    if len(block["lines"]) == 1 and "\n" not in text:
        x0, x1 = block["bbox"][0], block["bbox"][2]
        limit = block.get("limit_x") or x1 + 0.08 * (x1 - x0)
        limit = max(limit, x1)
        width = choice.measure(text, size0)
        scale = min(1.0, (limit - x0) / width) if width else 1.0
        if scale >= _MIN_SCALE:
            x = x0
            if _is_centred(block, page):
                x = (x0 + x1) / 2 - width * scale / 2
            page.insert_text(pymupdf.Point(x, span["origin"][1]), text, fontname=name, fontsize=size0 * scale, color=color)
            return
    pitch = _line_pitch(block)
    lineheight = pitch / size0 if pitch and 0.9 <= pitch / size0 <= 2.5 else None

    align = 1 if _is_centred(block, page) else 0

    def attempt(box: pymupdf.Rect, size: float) -> bool:
        return page.insert_textbox(
            box, text, fontname=name, fontsize=size, color=color, align=align, lineheight=lineheight
        ) >= 0

    # a single line (a table cell, a heading) may run on to the next thing on its line
    limit_x = block.get("limit_x")
    if limit_x and limit_x > rect.x1 and align == 0:
        rect = pymupdf.Rect(rect.x0, rect.y0, limit_x, rect.y1)

    size = size0
    while size >= size0 * _MIN_SCALE:
        if attempt(rect, size):
            return
        size *= 0.93
    # still too long: let it run down the page a little, at the smallest size
    taller = pymupdf.Rect(rect.x0, rect.y0, rect.x1, min(page.rect.y1 - 18, rect.y1 + rect.height * 1.5 + size0 * 2))
    if not attempt(taller, size0 * _MIN_SCALE):
        raise LayoutUnsupported("the new text doesn't fit where the old text was")


def _reword(
    doc: pymupdf.Document, instruction: str, llm: ParagraphLLM, book: _FontBook
) -> Iterator[EditProgress | LayoutResult]:
    items: list[tuple[int, int, dict, str]] = []  # (id, page number, block, text)
    for page_number, page in enumerate(doc):
        for block in _text_units(page):
            text = _block_text(block)
            if len(text) >= 2 and any(c.isalpha() for c in text):
                items.append((len(items), page_number, block, text))
    if not items:
        raise LayoutUnsupported("it has no text layer to edit (is it a scan?)")
    if len(items) > MAX_PARAGRAPHS:
        raise LayoutUnsupported(f"it is too long to edit this way ({len(items)} text blocks)")

    changes: dict[int, str] = {}
    texts = {ident: text for ident, _, _, text in items}
    todo = [(ident, text) for ident, _, _, text in items]
    done = 0
    for batch in batches(todo):
        yield EditProgress(f"Rewriting text {done + 1}–{done + len(batch)} of {len(todo)}")
        for ident, new_text in reword_batch(llm, instruction, batch).items():
            new_text = " ".join(new_text.split())
            if new_text != texts[ident]:
                changes[ident] = new_text
        done += len(batch)
    if not changes:
        raise ValueError("nothing in the file needed to change for that request")
    if any(_needs_shaping(t) for t in changes.values()):
        raise LayoutUnsupported("the new text uses a script that can't be set in place")

    by_page: dict[int, list[tuple[dict, str]]] = {}
    for ident, page_number, block, _ in items:
        if ident in changes:
            by_page.setdefault(page_number, []).append((block, changes[ident]))
    for page_number, edits in by_page.items():
        page = doc[page_number]
        fonts = _PageFonts(book, page)
        for block, text in edits:
            fonts.choose(_first_span(block), text)  # fail before anything on the page is cleared
        rects = [_span_rect(s) for block, _ in edits for line in block["lines"] for s in line["spans"] if s["text"].strip()]
        _clear(page, rects)
        for block, text in edits:
            _write_block(page, fonts, block, text)
    yield LayoutResult(changed=len(changes))


def _save(doc: pymupdf.Document, dst: Path) -> None:
    """Save, keeping only the letters of each added font (a whole font is
    hundreds of kilobytes per edit)."""
    try:
        doc.subset_fonts()
    except Exception:
        pass  # a larger file is better than no file
    doc.save(str(dst), garbage=4, deflate=True)


def edit_pdf(src: Path, dst: Path, instruction: str, llm: ParagraphLLM) -> Iterator[EditProgress | LayoutResult]:
    doc = pymupdf.open(str(src))
    try:
        if doc.needs_pass:
            raise LayoutUnsupported("it is password-protected")
        book = _FontBook(doc)

        replacement = parse_replacement(instruction)
        if replacement is not None:
            count = sum(_replace_on_page(page, replacement, book) for page in doc)
            if count:
                _save(doc, dst)
                yield LayoutResult(changed=count, replaced=replacement, replacements=count)
                return
            if looks_like_name(replacement.old):
                # maybe split over two lines, which only the whole-text edit can match
                raise LayoutUnsupported(f'"{replacement.old}" isn\'t on a single line of the PDF')

        result: LayoutResult | None = None
        for update in _reword(doc, instruction, llm, book):
            if isinstance(update, LayoutResult):
                result = update
            else:
                yield update
        assert result is not None
        _save(doc, dst)
        yield result
    finally:
        doc.close()

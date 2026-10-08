"""Restyle a .docx: fonts, sizes, colours, alignment, spacing, table borders and
page margins change; not one word of the text does. The model only decides
what to change (styling.py); this file makes the change in the document's own
XML, so tables, images, headers and numbering are untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from app.editing.docx_layout import _P, _header_footer_paragraphs, _own_runs
from app.editing.engine import EditProgress
from app.editing.layout import ParagraphLLM
from app.editing.styling import StyleOp, describe, plan_style_changes

_ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}
_HIGHLIGHT = {
    "yellow": WD_COLOR_INDEX.YELLOW, "green": WD_COLOR_INDEX.BRIGHT_GREEN, "cyan": WD_COLOR_INDEX.TURQUOISE,
    "magenta": WD_COLOR_INDEX.PINK, "blue": WD_COLOR_INDEX.BLUE, "red": WD_COLOR_INDEX.RED,
    "gray": WD_COLOR_INDEX.GRAY_25, "none": None,
}
_TC = qn("w:tc")
_DEFAULT_SIZE_PT = 11.0

_SHD_AFTER = (
    "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap", "w:overflowPunct", "w:topLinePunct",
    "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind",
    "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
    "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
)


@dataclass(frozen=True)
class StyleResult:
    ops: list[StyleOp]
    changed: int  # paragraphs, tables and sections that were touched
    skipped: list[str] = field(default_factory=list)  # targets the document has none of
    ignored: list[str] = field(default_factory=list)  # requested settings that were not valid

    @property
    def summary(self) -> str:
        return describe(self.ops)


# ---------- reading the document ----------

def _in_table(paragraph) -> bool:
    return any(a.tag == _TC for a in paragraph._p.iterancestors())


def _style_chain(paragraph) -> Iterator:
    style = paragraph.style
    while style is not None:
        yield style
        style = style.base_style


def _role(paragraph) -> tuple[str, int | None]:
    if _in_table(paragraph):
        return "tables", None
    name = (paragraph.style.name if paragraph.style is not None else "").lower()
    if name in ("title", "subtitle"):
        return "title", None
    if name.startswith("heading"):
        digits = re.search(r"(\d+)$", name)
        return "headings", int(digits.group(1)) if digits else 1
    pPr = paragraph._p.pPr
    if "list" in name or (pPr is not None and pPr.numPr is not None):
        return "lists", None
    return "body", None


def _default_size(doc) -> float:
    found = doc.styles.element.xpath("w:docDefaults/w:rPrDefault/w:rPr/w:sz/@w:val")
    return int(found[0]) / 2 if found else _DEFAULT_SIZE_PT


def _effective_size(doc, paragraph, run: Run) -> float:
    if run.font.size:
        return run.font.size.pt
    for style in _style_chain(paragraph):
        if style.font.size:
            return style.font.size.pt
    return _default_size(doc)


def _effective_font(doc, paragraph, run: Run) -> str:
    if run.font.name:
        return run.font.name
    for style in _style_chain(paragraph):
        if style.font.name:
            return style.font.name
    found = doc.styles.element.xpath("w:docDefaults/w:rPrDefault/w:rPr/w:rFonts/@w:ascii")
    return found[0] if found else "the default font"


def _paragraphs(doc) -> list[Paragraph]:
    return [Paragraph(p, doc) for p in doc.element.body.iter(_P)]


def describe_document(doc) -> str:
    """What the model needs to know to plan: which kinds of text there are and
    how they look now."""
    groups: dict[str, list[Paragraph]] = {}
    for paragraph in _paragraphs(doc):
        if paragraph.text.strip():
            groups.setdefault(_role(paragraph)[0], []).append(paragraph)

    lines = []
    section = doc.sections[0]
    orientation = "landscape" if section.orientation == WD_ORIENT.LANDSCAPE else "portrait"
    margins = ", ".join(
        f"{side} {getattr(section, side + '_margin').inches:.2f}" for side in ("top", "bottom", "left", "right")
        if getattr(section, side + "_margin") is not None
    )
    lines.append(f"Page: {orientation}, margins in inches: {margins}")
    for role in ("title", "headings", "body", "lists", "tables"):
        paragraphs = groups.get(role)
        if not paragraphs:
            lines.append(f"{role}: none")
            continue
        first = paragraphs[0]
        runs = _own_runs(first._p)
        look = ""
        if runs:
            run = Run(runs[0], first)
            look = f", now {_effective_font(doc, first, run)} {_effective_size(doc, first, run):g} pt"
        lines.append(f'{role}: {len(paragraphs)} paragraph(s), e.g. "{first.text.strip()[:40]}"{look}')
    return "\n".join(lines)


# ---------- changing it ----------

def _set_font_name(run: Run, name: str) -> None:
    fonts = run._r.get_or_add_rPr().get_or_add_rFonts()
    for attribute in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        fonts.attrib.pop(qn(f"w:{attribute}"), None)
    for attribute in ("ascii", "hAnsi", "eastAsia", "cs"):
        fonts.set(qn(f"w:{attribute}"), name)


def _style_run(doc, paragraph, run: Run, p: dict) -> None:
    if "font" in p:
        _set_font_name(run, p["font"])
    if "size" in p:
        run.font.size = Pt(p["size"])
    elif "size_scale" in p:
        run.font.size = Pt(min(96.0, max(4.0, round(_effective_size(doc, paragraph, run) * p["size_scale"] * 2) / 2)))
    if "color" in p:
        run.font.color.rgb = RGBColor.from_string(p["color"])
    for flag, attribute in (("bold", "bold"), ("italic", "italic"), ("underline", "underline"), ("caps", "all_caps")):
        if flag in p:
            setattr(run.font, attribute, p[flag])
    if "highlight" in p:
        run.font.highlight_color = _HIGHLIGHT[p["highlight"]]


def _shade_paragraph(paragraph: Paragraph, fill: str) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    for old in pPr.findall(qn("w:shd")):
        pPr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pPr.insert_element_before(shd, *_SHD_AFTER)


def _style_paragraph(paragraph: Paragraph, p: dict) -> None:
    if "align" in p:
        paragraph.alignment = _ALIGN[p["align"]]
    fmt = paragraph.paragraph_format
    if "line_spacing" in p:
        fmt.line_spacing = p["line_spacing"]
    if "space_before" in p:
        fmt.space_before = Pt(p["space_before"])
    if "space_after" in p:
        fmt.space_after = Pt(p["space_after"])
    if "indent_first_line" in p:
        fmt.first_line_indent = Pt(p["indent_first_line"])
    if "shading" in p:
        _shade_paragraph(paragraph, p["shading"])


def _matches(op: StyleOp, role: str, level: int | None) -> bool:
    if op.target == "all":
        return True
    if op.target != role:
        return False
    return op.level is None or op.level == level


def _set_table_borders(table, on: bool) -> None:
    tblPr = table._tbl.tblPr
    for old in tblPr.findall(qn("w:tblBorders")):
        tblPr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        edge = OxmlElement(f"w:{side}")
        edge.set(qn("w:val"), "single" if on else "nil")
        if on:
            edge.set(qn("w:sz"), "4")
            edge.set(qn("w:space"), "0")
            edge.set(qn("w:color"), "808080")
        borders.append(edge)
    tblPr.insert_element_before(
        borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription", "w:tblPrChange"
    )


def _fill_header_row(table, fill: str) -> None:
    for cell in table.rows[0].cells:
        tcPr = cell._tc.get_or_add_tcPr()
        for old in tcPr.findall(qn("w:shd")):
            tcPr.remove(old)
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill)
        tcPr.insert_element_before(
            shd, "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark", "w:headers",
            "w:cellIns", "w:cellDel", "w:cellMerge", "w:tcPrChange",
        )


def _set_page(doc, p: dict) -> int:
    for section in doc.sections:
        for side, value in p.get("margins", {}).items():
            setattr(section, f"{side}_margin", Inches(value))
        want = p.get("orientation")
        if want and (want == "landscape") != (section.orientation == WD_ORIENT.LANDSCAPE):
            section.orientation = WD_ORIENT.LANDSCAPE if want == "landscape" else WD_ORIENT.PORTRAIT
            section.page_width, section.page_height = section.page_height, section.page_width
    return len(doc.sections)


def apply_ops(doc, ops: list[StyleOp]) -> tuple[int, list[str]]:
    """Make the changes. Returns how many things were touched and the targets
    the document has nothing for."""
    touched = 0
    matched: set[int] = set()
    paragraphs = [(p, *_role(p)) for p in _paragraphs(doc) if p.text.strip()]

    for index, op in enumerate(ops):
        if op.target == "page":
            touched += _set_page(doc, op.props)
            matched.add(index)
            continue
        if op.target == "tables":
            for table in doc.tables:
                if "table_borders" in op.props:
                    _set_table_borders(table, op.props["table_borders"])
                if "table_header_fill" in op.props:
                    _fill_header_row(table, op.props["table_header_fill"])
                touched += 1
                matched.add(index)
        if op.target == "header_footer":
            targets = [(Paragraph(p, doc), "header_footer", None) for p in _header_footer_paragraphs(doc)]
            targets = [(p, r, l) for p, r, l in targets if p.text.strip()]
        else:
            targets = [(p, r, l) for p, r, l in paragraphs if _matches(op, r, l)]
        for paragraph, _, _ in targets:
            _style_paragraph(paragraph, op.props)
            for element in _own_runs(paragraph._p):
                _style_run(doc, paragraph, Run(element, paragraph), op.props)
            touched += 1
            matched.add(index)

    label = {"title": "a title", "headings": "headings", "body": "body text", "lists": "lists", "tables": "tables",
             "header_footer": "a header or footer", "all": "any text"}
    skipped = [label[op.target] for i, op in enumerate(ops) if i not in matched and op.target in label]
    return touched, skipped


def restyle_docx(src: Path, dst: Path, instruction: str, llm: ParagraphLLM) -> Iterator[EditProgress | StyleResult]:
    doc = Document(str(src))
    yield EditProgress("Working out the style changes")
    ops, ignored = plan_style_changes(llm, instruction, describe_document(doc))
    if not ops:
        raise ValueError(
            "I couldn't tell which style changes you want. Try something like "
            "“make the headings dark blue and use Arial 11 for the body”"
        )
    touched, skipped = apply_ops(doc, ops)
    if not touched:
        raise ValueError(f"the file has no {skipped[0] if skipped else 'text'} to change")
    doc.save(str(dst))
    yield StyleResult(ops, touched, skipped, ignored)

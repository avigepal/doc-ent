"""One-off generator for reference.docx — the pandoc --reference-doc
template that styles .docx report exports. Run manually whenever the
style needs tweaking:

    python app/export/templates/make_reference_docx.py

pandoc reuses this file's styles (Title/Heading 1-3/Normal/Quote/Source
Code) for every docx export; it does not read this script at export
time, so the generated reference.docx is the actual committed asset.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

INDEX_BLUE = RGBColor(0x2B, 0x3A, 0x67)
LOCATOR_BROWN = RGBColor(0xA8, 0x68, 0x1F)
INK = RGBColor(0x16, 0x18, 0x1D)
INK_SOFT = RGBColor(0x52, 0x55, 0x5C)
LINE_GRAY = RGBColor(0xD8, 0xD5, 0xCC)


def _set_east_asian_font(run, name: str) -> None:
    rpr = run.font.element.rPr
    if rpr is None:
        rpr = run.font.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs"):
        rfonts.set(qn(attr), name)


def build() -> Path:
    doc = Document()

    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.font.color.rgb = INK
    normal.paragraph_format.space_after = Pt(8)

    title = doc.styles["Title"]
    title.font.name = "Calibri"
    title.font.size = Pt(26)
    title.font.bold = True
    title.font.color.rgb = INDEX_BLUE
    title.paragraph_format.space_after = Pt(4)

    if "Subtitle" in doc.styles:
        subtitle = doc.styles["Subtitle"]
        subtitle.font.name = "Calibri"
        subtitle.font.size = Pt(13)
        subtitle.font.bold = False
        subtitle.font.color.rgb = LOCATOR_BROWN
        subtitle.paragraph_format.space_after = Pt(12)

    heading_sizes = {"Heading 1": 18, "Heading 2": 14, "Heading 3": 12}
    for name, size in heading_sizes.items():
        style = doc.styles[name]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = INDEX_BLUE
        style.paragraph_format.space_before = Pt(16)
        style.paragraph_format.space_after = Pt(6)

    if "Quote" in doc.styles:
        quote = doc.styles["Quote"]
        quote.font.name = "Calibri"
        quote.font.italic = True
        quote.font.color.rgb = INK_SOFT

    # pandoc maps fenced code blocks to the "Source Code" paragraph
    # style for docx output — give it a monospace font and a subtle
    # shaded look to match the PDF template's framed code blocks.
    try:
        code = doc.styles["Source Code"]
    except KeyError:
        from docx.enum.style import WD_STYLE_TYPE

        code = doc.styles.add_style("Source Code", WD_STYLE_TYPE.PARAGRAPH)
    code.font.name = "Consolas"
    code.font.size = Pt(9.5)
    code.font.color.rgb = INK

    # Title-page header block: accent rule, wordmark, generated-by line —
    # pandoc inserts the metadata title/subtitle paragraphs right after
    # whatever content already exists in the reference doc's body, so we
    # seed it with the rule + wordmark here.
    rule = doc.add_paragraph()
    rule_run = rule.add_run("_" * 64)
    rule_run.font.color.rgb = INDEX_BLUE
    rule_run.font.size = Pt(8)

    wordmark = doc.add_paragraph()
    wordmark.alignment = WD_ALIGN_PARAGRAPH.LEFT
    wm_run = wordmark.add_run("DOCENT — INTELLIGENT REPORT")
    wm_run.font.name = "Calibri"
    wm_run.font.size = Pt(11)
    wm_run.font.bold = True
    wm_run.font.color.rgb = LOCATOR_BROWN

    out_path = Path(__file__).parent / "reference.docx"
    doc.save(out_path)
    return out_path


if __name__ == "__main__":
    path = build()
    print(f"wrote {path}")

import json

import pytest

docx = pytest.importorskip("docx")

from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.shared import Pt  # noqa: E402

from app.editing.docx_style import StyleResult, describe_document, restyle_docx  # noqa: E402


class FakeModel:
    def __init__(self, plan):
        self.plan = plan

    def chat(self, system, user, temperature=0.2, **kwargs):
        return self.plan if isinstance(self.plan, str) else json.dumps(self.plan)


def make(path):
    d = docx.Document()
    d.add_heading("Quotation", 0)
    d.add_heading("Items", 1)
    p = d.add_paragraph("The ")
    bold = p.add_run("Mac Studio")
    bold.bold = True
    p.add_run(" is quiet.")
    d.add_paragraph("Quiet and small", style="List Bullet")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "Model"
    t.cell(0, 1).text = "Price"
    t.cell(1, 0).text = "Mac Studio"
    t.cell(1, 1).text = "6,29,900"
    d.sections[0].header.paragraphs[0].text = "Confidential"
    d.save(str(path))


def run(src, dst, plan):
    results = [u for u in restyle_docx(src, dst, "restyle it", FakeModel(plan)) if isinstance(u, StyleResult)]
    return results[-1]


def texts(document):
    return [p.text for p in document.paragraphs] + [c.text for row in document.tables[0].rows for c in row.cells]


def test_the_model_is_told_what_the_document_contains(tmp_path):
    src = tmp_path / "a.docx"
    make(src)
    facts = describe_document(docx.Document(str(src)))

    assert "headings: 1 paragraph(s)" in facts and "Items" in facts
    assert "body: 1 paragraph(s)" in facts and "lists: 1 paragraph(s)" in facts
    assert "Page: portrait" in facts


def test_headings_change_colour_and_weight_and_nothing_else_does(tmp_path):
    src, dst = tmp_path / "a.docx", tmp_path / "b.docx"
    make(src)
    result = run(src, dst, {"operations": [{"target": "headings", "set": {"color": "#1F3864", "font": "Arial", "size": 20}}]})

    out = docx.Document(str(dst))
    assert result.changed == 1
    heading = out.paragraphs[1]
    assert [str(r.font.color.rgb) for r in heading.runs] == ["1F3864"]
    assert heading.runs[0].font.name == "Arial" and heading.runs[0].font.size == Pt(20)
    assert out.paragraphs[0].runs[0].font.color.rgb is None  # the title is not a heading
    assert out.paragraphs[2].runs[0].font.name is None  # nor is the body
    assert texts(out) == texts(docx.Document(str(src)))  # not one word changed


def test_body_text_keeps_its_own_bold_while_getting_the_new_font(tmp_path):
    src, dst = tmp_path / "a.docx", tmp_path / "b.docx"
    make(src)
    run(src, dst, {"operations": [{"target": "body", "set": {"font": "Georgia", "size": 13, "align": "justify", "line_spacing": 1.5}}]})

    paragraph = docx.Document(str(dst)).paragraphs[2]
    assert [(r.text, r.bold, r.font.name) for r in paragraph.runs] == [
        ("The ", None, "Georgia"), ("Mac Studio", True, "Georgia"), (" is quiet.", None, "Georgia"),
    ]
    assert paragraph.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY
    assert paragraph.paragraph_format.line_spacing == 1.5


def test_relative_sizes_start_from_the_size_the_text_has_now(tmp_path):
    src, dst = tmp_path / "a.docx", tmp_path / "b.docx"
    d = docx.Document()
    p = d.add_paragraph()
    p.add_run("ten").font.size = Pt(10)
    p.add_run(" default")
    d.save(str(src))

    run(src, dst, {"operations": [{"target": "body", "set": {"size_scale": 1.5}}]})

    runs = docx.Document(str(dst)).paragraphs[0].runs
    assert runs[0].font.size == Pt(15) and runs[1].font.size == Pt(16.5)  # 11 pt default x 1.5


def test_tables_get_borders_and_a_shaded_header_row(tmp_path):
    src, dst = tmp_path / "a.docx", tmp_path / "b.docx"
    make(src)
    run(src, dst, {"operations": [{"target": "tables", "set": {"table_borders": True, "table_header_fill": "#DDDDDD"}}]})

    table = docx.Document(str(dst)).tables[0]
    borders = table._tbl.tblPr.find(qn("w:tblBorders"))
    assert {e.get(qn("w:val")) for e in borders} == {"single"}
    fills = [c._tc.tcPr.find(qn("w:shd")).get(qn("w:fill")) for c in table.rows[0].cells]
    assert fills == ["DDDDDD", "DDDDDD"]
    assert table.rows[1].cells[0]._tc.tcPr is None or table.rows[1].cells[0]._tc.tcPr.find(qn("w:shd")) is None


def test_page_margins_and_orientation(tmp_path):
    src, dst = tmp_path / "a.docx", tmp_path / "b.docx"
    make(src)
    before = docx.Document(str(src)).sections[0]
    width, height = before.page_width, before.page_height

    run(src, dst, {"operations": [{"target": "page", "set": {"margins": {"left": 2, "right": 2}, "orientation": "landscape"}}]})

    section = docx.Document(str(dst)).sections[0]
    assert section.left_margin.inches == 2 and section.right_margin.inches == 2
    assert section.page_width == height and section.page_height == width


def test_a_paragraph_background_is_written_in_a_valid_position(tmp_path):
    src, dst = tmp_path / "a.docx", tmp_path / "b.docx"
    make(src)
    run(src, dst, {"operations": [{"target": "body", "set": {"shading": "#FFF2CC", "space_after": 12}}]})

    pPr = docx.Document(str(dst)).paragraphs[2]._p.pPr
    children = [c.tag.split("}")[1] for c in pPr]
    assert children.index("shd") < children.index("spacing")


def test_headers_and_footers_can_be_restyled(tmp_path):
    src, dst = tmp_path / "a.docx", tmp_path / "b.docx"
    make(src)
    run(src, dst, {"operations": [{"target": "header_footer", "set": {"color": "#7F7F7F", "size": 8}}]})

    run_ = docx.Document(str(dst)).sections[0].header.paragraphs[0].runs[0]
    assert str(run_.font.color.rgb) == "7F7F7F" and run_.font.size == Pt(8)


def test_a_target_the_document_does_not_have_is_reported_not_faked(tmp_path):
    src, dst = tmp_path / "a.docx", tmp_path / "b.docx"
    d = docx.Document()
    d.add_paragraph("just text")
    d.save(str(src))

    result = run(src, dst, {"operations": [{"target": "headings", "set": {"bold": True}}, {"target": "body", "set": {"italic": True}}]})

    assert result.skipped == ["headings"]
    assert docx.Document(str(dst)).paragraphs[0].runs[0].italic is True


def test_nothing_to_change_is_an_error_not_an_unchanged_file(tmp_path):
    src = tmp_path / "a.docx"
    d = docx.Document()
    d.add_paragraph("just text")
    d.save(str(src))

    with pytest.raises(ValueError, match="no headings"):
        list(restyle_docx(src, tmp_path / "b.docx", "make headings red", FakeModel({"operations": [{"target": "headings", "set": {"bold": True}}]})))


def test_a_request_the_model_cannot_turn_into_changes_says_how_to_ask(tmp_path):
    src = tmp_path / "a.docx"
    make(src)
    with pytest.raises(ValueError, match="make the headings dark blue"):
        list(restyle_docx(src, tmp_path / "b.docx", "do something", FakeModel({"operations": []})))

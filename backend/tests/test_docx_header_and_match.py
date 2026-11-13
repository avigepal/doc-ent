import pytest

docx = pytest.importorskip("docx")

from app.editing.docx_ops import wants_exact_edit  # noqa: E402
from app.editing.docx_style import describe_document  # noqa: E402


def test_a_request_about_the_header_goes_to_the_exact_editor():
    assert wants_exact_edit("put Docent Ltd in the header", needs_structure=False)
    assert wants_exact_edit("add a footer with the page name", needs_structure=False)


def test_the_style_facts_list_placeholder_text_inside_a_table():
    d = docx.Document()
    d.add_paragraph("Intro")
    table = d.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "[Company logo]"

    facts = describe_document(d)

    assert 'short text, copy a match from here exactly:' in facts
    assert '"[Company logo]"' in facts


def test_the_text_to_match_is_taken_from_the_settings_when_the_model_puts_it_there():
    from app.editing.styling import sanitize  # noqa: E402

    ops, ignored = sanitize({"operations": [{"target": "matching", "set": {"match": "[Company logo]", "color": "#FF0000"}}]})

    assert not ignored
    assert ops[0].match == "[Company logo]" and ops[0].props == {"color": "FF0000"}

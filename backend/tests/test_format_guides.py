from app.editing.docx_ops import OPS_SYSTEM_PROMPT as DOCX_PROMPT
from app.editing.guides import FORMAT_GUIDES, format_guide
from app.editing.xlsx_ops import OPS_SYSTEM_PROMPT as XLSX_PROMPT


def test_a_format_with_a_guide_gets_it_after_the_operation_rules():
    assert format_guide("docx").startswith("\n\nWord document rules:")
    assert format_guide("xlsx").startswith("\n\nExcel workbook rules:")


def test_a_format_without_a_guide_adds_nothing():
    assert format_guide("pdf") == ""
    assert format_guide("") == ""


def test_the_editors_send_their_guide_with_the_operation_rules():
    # the editors build their system prompt as: operation rules + guide
    assert (DOCX_PROMPT + format_guide("docx")).endswith(FORMAT_GUIDES["docx"])
    assert (XLSX_PROMPT + format_guide("xlsx")).endswith(FORMAT_GUIDES["xlsx"])


def test_the_xlsx_guide_keeps_text_out_of_formulas():
    assert "never a string that starts with" in FORMAT_GUIDES["xlsx"]

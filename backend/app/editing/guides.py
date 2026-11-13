"""Per-format guides for the exact editors. Each guide is added to the system
prompt of its editor (docx_ops.py, xlsx_ops.py) so the model knows what the
operations in that format can and cannot safely do. The guide only describes
rules; the code still checks every operation it is given."""

from __future__ import annotations

FORMAT_GUIDES: dict[str, str] = {
    "docx": (
        "Word document rules:\n"
        "- A table is changed cell by cell (set_cell) or extended with a new table (insert_table_after). "
        "Do not rebuild a whole table as paragraphs.\n"
        "- Headings are added with style \"heading\" and a level from 1 to 3; body text uses \"paragraph\"; "
        "lists use \"bullet\" or \"numbered\".\n"
        "- Replace a paragraph only with its complete new text. Keep the wording the request does not "
        "ask to change.\n"
        "- Do not delete a table or heading unless the request names it."
    ),
    "xlsx": (
        "Excel workbook rules:\n"
        "- Prefer a formula for any value that depends on other cells, so it updates when the data changes.\n"
        "- Text copied from a document is text: use \"value\" for it, never a string that starts with \"=\".\n"
        "- Formulas may refer only to cells in this workbook.\n"
        "- Sorting or deleting rows by a column of formulas needs their calculated values, so say so "
        "if the sheet has no calculated values yet.\n"
        "- Leave header rows and total rows alone unless the request names them."
    ),
}


def format_guide(fmt: str) -> str:
    """The guide text for a format, or an empty string when there is none."""
    guide = FORMAT_GUIDES.get(fmt)
    return f"\n\n{guide}" if guide else ""

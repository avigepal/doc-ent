"""Edit an Excel file: exact changes to cells, columns and rows, with everything else left as it was.

The model never rewrites the workbook. It is shown each sheet as a short description plus the first
rows (with their real row numbers and column letters) and answers with a short list of operations:

  set_cell          one cell: a value, or a formula ("=B2*C2")
  add_column        a new column at the right of the data, with a header and a value or formula per row
  set_column        fill or overwrite a column's data cells with a value or a formula
  transform_column  upper / lower / title case, trim spaces, or turn text into numbers
  find_replace      replace text in cells, in one column or all of them
  delete_rows_where remove the rows whose cell in a column matches a test
  sort              sort the data rows by a column
  rename_header     the text at the top of a column
  rename_sheet      a sheet's name
  format_column     a column's number format (currency, percent, date...)
  reword_cells      rewrite the text of a column cell by cell with the model (translate, fix spelling)

Code applies each one with openpyxl, which keeps fonts, colours, widths, filters and merged cells.
It does not keep charts, pivot tables, macros or images: the note for the reply says so when the
original had any. A formula comes from the model, so it is checked first: nothing that reaches out to
another file, a web address or a program is written.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from openpyxl import load_workbook
from openpyxl.formula.translate import Translator
from openpyxl.utils import column_index_from_string, get_column_letter

from app.editing.engine import EditProgress
from app.editing.layout import LayoutUnsupported, ParagraphLLM, batches, chat_json, reword_batch

MAX_OPERATIONS = 60
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_SAMPLE_ROWS = 15
MAX_SHOWN_COLUMNS = 40
MAX_SHOWN_SHEETS = 10
MAX_CELL_CHARS = 60
MAX_PROMPT_CHARS = 30_000
MAX_REWORD_CELLS = 400
MAX_ROWS_FOR_SORT = 200_000

OPERATIONS = (
    "set_cell",
    "add_column",
    "set_column",
    "transform_column",
    "find_replace",
    "delete_rows_where",
    "sort",
    "rename_header",
    "rename_sheet",
    "format_column",
    "reword_cells",
)

TRANSFORMS = ("upper", "lower", "title", "trim", "to_number")
TESTS = ("equals", "not_equals", "contains", "not_contains", "empty", "not_empty", "gt", "gte", "lt", "lte")
NUMBER_FORMATS = {
    "currency": '"$"#,##0.00',
    "percent": "0.00%",
    "integer": "#,##0",
    "decimal": "0.00",
    "date": "yyyy-mm-dd",
    "text": "@",
    "general": "General",
}

_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d{1,7})$")
_LETTERS = re.compile(r"^[A-Za-z]{1,3}$")

# A formula may do arithmetic, lookups and text work. It may not reach out of the workbook: to another
# file ([book.xlsx]), a web address, a program (DDE: =cmd|...), or call a function that fetches or runs things.
_UNSAFE_FORMULA = re.compile(
    r"\[|https?:|ftp:|file:|\\\\|\||\b(?:WEBSERVICE|HYPERLINK|IMPORTXML|IMPORTDATA|IMPORTHTML|IMPORTRANGE|FILTERXML|"
    r"EXEC|CALL|REGISTER|REGISTER\.ID|RTD|DDE|SHELL|INDIRECT|HTML|MSQUERY|SQL\.REQUEST)\s*\(",
    re.IGNORECASE,
)


def safe_formula(formula: str) -> bool:
    return formula.startswith("=") and len(formula) <= 500 and not _UNSAFE_FORMULA.search(formula)


# ---------- reading the workbook for the model ----------


@dataclass
class SheetInfo:
    name: str
    header_row: int
    last_row: int
    last_col: int
    has_formulas: bool
    footer: int = 0  # total rows at the bottom ("Total", =SUM(...)): not data, never sorted or removed


_TOTAL_LABEL = re.compile(r"^\s*(?:grand\s+|sub\s*)?(?:total|sum|average|avg|mean|count)\b", re.IGNORECASE)
_AGGREGATE = re.compile(r"\b(?:SUM|AVERAGE|COUNT|COUNTA|MIN|MAX|SUBTOTAL)\s*\([^)]*:", re.IGNORECASE)


def _looks_like_total_row(ws, row: int, last_col: int) -> bool:
    cells = [ws.cell(row=row, column=c) for c in range(1, last_col + 1)]
    filled = [c for c in cells if not _is_blank(c.value)]
    if not filled:
        return False
    label = next((c.value for c in filled if isinstance(c.value, str) and c.data_type != "f"), None)
    if label is not None and _TOTAL_LABEL.match(label):
        return True
    formulas = [c for c in filled if c.data_type == "f"]
    return bool(formulas) and len(formulas) == len(filled) and any(_AGGREGATE.search(str(c.value)) for c in formulas)


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, (datetime, date)):
        value = value.isoformat()[:10]
    return str(value)


def _is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def sheet_info(ws) -> SheetInfo:
    last_row = 0
    last_col = 0
    has_formulas = False
    for row in ws.iter_rows():
        for cell in row:
            if not _is_blank(cell.value):
                last_row = max(last_row, cell.row)
                last_col = max(last_col, cell.column)
                if cell.data_type == "f":
                    has_formulas = True

    # the header is the first row near the top that has about as many filled cells as the fullest of them
    counts = {}
    for r in range(1, min(last_row, 10) + 1):
        counts[r] = sum(1 for c in range(1, last_col + 1) if not _is_blank(ws.cell(row=r, column=c).value))
    best = max(counts.values(), default=0)
    header_row = next((r for r, n in counts.items() if best >= 2 and n >= max(2, 0.6 * best)), 1)

    footer = 0
    while footer < 3 and last_row - footer > header_row and _looks_like_total_row(ws, last_row - footer, last_col):
        footer += 1
    return SheetInfo(ws.title, header_row, last_row, last_col, has_formulas, footer)


def describe_workbook(wb) -> str:
    out: list[str] = []
    for ws in wb.worksheets[:MAX_SHOWN_SHEETS]:
        info = sheet_info(ws)
        if info.last_row == 0:
            out.append(f'Sheet "{ws.title}": empty')
            continue
        shown = min(info.last_col, MAX_SHOWN_COLUMNS)
        letters = [get_column_letter(c) for c in range(1, shown + 1)]
        out.append(
            f'Sheet "{ws.title}": data in rows 1-{info.last_row}, columns A-{get_column_letter(info.last_col)}, '
            f"header row {info.header_row}"
            + (", has formulas" if info.has_formulas else "")
            + (f", total row at {info.last_row - info.footer + 1}-{info.last_row}" if info.footer else "")
        )
        out.append("columns: " + "  ".join(f"{L}={_text(ws[f'{L}{info.header_row}'].value)[:MAX_CELL_CHARS]}" for L in letters))
        out.append("row | " + " | ".join(letters))
        for r in range(1, min(info.last_row, info.header_row + MAX_SAMPLE_ROWS) + 1):
            values = [_text(ws.cell(row=r, column=c).value).replace("\n", " ")[:MAX_CELL_CHARS] for c in range(1, shown + 1)]
            out.append(f"{r:>3} | " + " | ".join(values))
        if info.last_row > info.header_row + MAX_SAMPLE_ROWS:
            out.append(f"... ({info.last_row - info.header_row - MAX_SAMPLE_ROWS} more rows)")
        out.append("")
    if len(wb.worksheets) > MAX_SHOWN_SHEETS:
        out.append(f"({len(wb.worksheets) - MAX_SHOWN_SHEETS} more sheets not shown)")
    return "\n".join(out).strip()


# ---------- operations ----------

OPS_SYSTEM_PROMPT = (
    "You edit an Excel workbook with exact operations. You get a request and a description of each sheet "
    "(its header row, column letters, and the first rows with their real row numbers). Reply with JSON only: "
    '{"operations": [ ... ]}\n'
    'Every operation has "op" and "sheet" (the sheet name; leave it out when there is one sheet). '
    '"column" is a column letter ("C") or the header text. Operations:\n'
    '  {"op": "set_cell", "cell": "B3", "value": "text, number, or a formula like =B2*C2"}\n'
    '  {"op": "add_column", "header": "Total", "formula": "=B{row}*C{row}"}   a new column after the last one; '
    '{row} is each data row\'s number; use "value" instead of "formula" for a constant\n'
    '  {"op": "set_column", "column": "D", "formula": "=B{row}*2"}   fill a column\'s data cells ("value" for a constant)\n'
    '  {"op": "transform_column", "column": "A", "transform": "upper|lower|title|trim|to_number"}\n'
    '  {"op": "find_replace", "find": "old", "replace": "new", "column": "A", "match_case": false}   '
    "(leave out column for the whole sheet)\n"
    '  {"op": "delete_rows_where", "column": "E", "test": "equals|not_equals|contains|not_contains|empty|not_empty|gt|gte|lt|lte", "value": "Closed"}\n'
    '  {"op": "sort", "column": "C", "descending": true}   sorts the data rows below the header\n'
    '  {"op": "rename_header", "column": "B", "new_header": "Quantity"}\n'
    '  {"op": "rename_sheet", "new_name": "2024"}\n'
    '  {"op": "format_column", "column": "C", "format": "currency|percent|integer|decimal|date|text"}\n'
    '  {"op": "reword_cells", "column": "B", "instruction": "translate to French"}   '
    "for work that needs reading each cell (translate, fix spelling, shorten)\n"
    "Rules:\n"
    "- Do exactly what the request says and nothing else. Prefer formulas over typing computed numbers.\n"
    "- Formulas use only this workbook: no other files, no web addresses, no external data.\n"
    "- Do not invent data. If the request needs information the workbook does not contain, say so.\n"
    "- Column letters and row numbers are exactly the ones listed.\n"
    '- If the request cannot be done with these operations, reply {"operations": [], "problem": "one sentence saying why"}.'
)


@dataclass
class Op:
    kind: str
    sheet: str
    column: str = ""
    cell: str = ""
    value: object = None
    formula: str = ""
    header: str = ""
    transform: str = ""
    find: str = ""
    replace: str = ""
    match_case: bool = False
    test: str = ""
    descending: bool = False
    new_name: str = ""
    number_format: str = ""
    instruction: str = ""


def _resolve_sheet(wb, name) -> str | None:
    if isinstance(name, str) and name.strip():
        for ws in wb.worksheets:
            if ws.title.lower() == name.strip().lower():
                return ws.title
        return None
    return wb.worksheets[0].title if len(wb.worksheets) == 1 else None


def parse_ops(data: dict, wb) -> tuple[list[Op], list[str]]:
    """The valid operations in the model's reply, and a sentence for each one dropped."""
    ops: list[Op] = []
    dropped: list[str] = []
    for raw in (data.get("operations") or [])[:MAX_OPERATIONS]:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("op", "")).strip().lower()
        if kind not in OPERATIONS:
            dropped.append(f"unknown operation “{raw.get('op')}”")
            continue
        sheet = _resolve_sheet(wb, raw.get("sheet"))
        if sheet is None:
            dropped.append(f"{kind}: no sheet called “{raw.get('sheet')}”" if raw.get("sheet") else f"{kind}: which sheet?")
            continue
        op = Op(kind, sheet)
        column = raw.get("column")
        if isinstance(column, int) and not isinstance(column, bool) and 1 <= column <= 200:
            column = get_column_letter(column)
        op.column = column.strip() if isinstance(column, str) else ""

        if kind == "set_cell":
            match = _CELL.match(str(raw.get("cell", "")).strip())
            value = raw.get("value")
            if not match or isinstance(value, (list, dict)):
                dropped.append("set_cell: no valid cell or value")
                continue
            op.cell = f"{match.group(1).upper()}{match.group(2)}"
            op.value = value
        elif kind in ("add_column", "set_column"):
            formula, value = raw.get("formula"), raw.get("value")
            if kind == "add_column":
                header = raw.get("header")
                if not isinstance(header, str) or not header.strip():
                    dropped.append("add_column: no header given")
                    continue
                op.header = header.strip()
            elif not op.column:
                dropped.append("set_column: no column given")
                continue
            if isinstance(formula, str) and formula.strip():
                op.formula = formula.strip()
            elif value is not None and not isinstance(value, (list, dict)):
                op.value = value
            elif kind == "set_column":
                dropped.append("set_column: no value or formula")
                continue
        elif kind == "transform_column":
            op.transform = str(raw.get("transform", "")).strip().lower()
            if not op.column or op.transform not in TRANSFORMS:
                dropped.append("transform_column: needs a column and one of " + ", ".join(TRANSFORMS))
                continue
        elif kind == "find_replace":
            find, replace = raw.get("find"), raw.get("replace")
            if not isinstance(find, str) or not find or not isinstance(replace, (str, int, float)):
                dropped.append("find_replace: needs the text to find and its replacement")
                continue
            op.find, op.replace, op.match_case = find, str(replace), raw.get("match_case") is True
        elif kind == "delete_rows_where":
            op.test = str(raw.get("test", "")).strip().lower()
            op.value = raw.get("value")
            if not op.column or op.test not in TESTS or (op.test not in ("empty", "not_empty") and op.value is None):
                dropped.append("delete_rows_where: needs a column, a test and a value")
                continue
        elif kind == "sort":
            if not op.column:
                dropped.append("sort: no column given")
                continue
            op.descending = raw.get("descending") is True
        elif kind == "rename_header":
            new = raw.get("new_header")
            if not op.column or not isinstance(new, str) or not new.strip():
                dropped.append("rename_header: needs a column and the new text")
                continue
            op.header = new.strip()
        elif kind == "rename_sheet":
            new = raw.get("new_name")
            if not isinstance(new, str) or not new.strip():
                dropped.append("rename_sheet: no new name given")
                continue
            op.new_name = re.sub(r"[\[\]:*?/\\]", " ", new).strip()[:31]
        elif kind == "format_column":
            fmt = str(raw.get("format", "")).strip().lower()
            if not op.column or fmt not in NUMBER_FORMATS:
                dropped.append("format_column: needs a column and one of " + ", ".join(NUMBER_FORMATS))
                continue
            op.number_format = NUMBER_FORMATS[fmt]
        elif kind == "reword_cells":
            instruction = raw.get("instruction")
            if not op.column or not isinstance(instruction, str) or not instruction.strip():
                dropped.append("reword_cells: needs a column and what to do")
                continue
            op.instruction = instruction.strip()
        ops.append(op)
    return ops, dropped


# ---------- applying them ----------


@dataclass
class Report:
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    changed_cells: int = 0


def resolve_column(ws, info: SheetInfo, ref: str) -> int | None:
    """A column number from a letter ("C") or a header text, or None."""
    ref = ref.strip()
    headers = {}
    for c in range(1, info.last_col + 1):
        text = _text(ws.cell(row=info.header_row, column=c).value).strip().lower()
        if text:
            headers.setdefault(text, c)
    if ref.lower() in headers:
        return headers[ref.lower()]
    if _LETTERS.match(ref):
        index = column_index_from_string(ref.upper())
        if index <= max(info.last_col, 1):
            return index
    return None


def _data_rows(info: SheetInfo) -> range:
    """The rows between the header and any total row at the bottom."""
    return range(info.header_row + 1, info.last_row - info.footer + 1)


_RANGE_END = re.compile(r"(\$?[A-Za-z]{1,3}\$?\d+):(\$?[A-Za-z]{1,3}\$?)(\d+)")


def _move_formula(formula: str, column: int, old_row: int, new_row: int) -> str:
    """The same formula as it must read one row down the sheet: =B2*C2 moved from row 2 to row 5 is =B5*C5."""
    letter = get_column_letter(column)
    return Translator(formula, origin=f"{letter}{old_row}").translate_formula(f"{letter}{new_row}")


def _shrink_totals(formula: str, old_last: int, new_last: int) -> str:
    """A total row's ranges follow the data when rows are removed: =SUM(B2:B9) is =SUM(B2:B7) after two go."""

    def fix(match):
        return f"{match.group(1)}:{match.group(2)}{new_last if int(match.group(3)) == old_last else match.group(3)}"

    return _RANGE_END.sub(fix, formula)


def _rewrite_rows(ws, info: SheetInfo, order: list[int], footer_shift: int = 0) -> None:
    """Put the data rows listed in `order` (old row numbers) one after another from the first data row,
    each with its formulas and formatting. Total rows at the bottom follow, `footer_shift` rows higher
    when rows were removed. Anything left over below is cleared."""
    first = info.header_row + 1
    last_data = info.last_row - info.footer
    footer_rows = list(range(last_data + 1, info.last_row + 1))
    sources = list(order) + footer_rows
    snapshot = {
        r: [(ws.cell(row=r, column=c).value, copy.copy(ws.cell(row=r, column=c)._style), ws.cell(row=r, column=c).data_type)
            for c in range(1, info.last_col + 1)]
        for r in sources
    }
    for position, old_row in enumerate(sources):
        new_row = first + position
        is_footer = old_row > last_data
        for column, (value, style, kind) in enumerate(snapshot[old_row], start=1):
            if kind == "f" and isinstance(value, str):
                value = (
                    _shrink_totals(value, last_data, last_data - footer_shift)
                    if is_footer
                    else _move_formula(value, column, old_row, new_row)
                )
            cell = ws.cell(row=new_row, column=column)
            cell.value = value
            cell._style = style
    end = first + len(sources) - 1
    if info.last_row > end:
        ws.delete_rows(end + 1, info.last_row - end)


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d.+\-eE]", "", value.replace(",", ""))
        if cleaned and re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", cleaned):
            return float(cleaned)
    return None


def _matches(value, test: str, target) -> bool:
    if test == "empty":
        return _is_blank(value)
    if test == "not_empty":
        return not _is_blank(value)
    a, b = _number(value), _number(target)
    if test in ("gt", "gte", "lt", "lte"):
        if a is None or b is None:
            return False
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[test]
    text, goal = _text(value).strip().lower(), _text(target).strip().lower()
    if test == "equals":
        return text == goal or (a is not None and b is not None and a == b)
    if test == "not_equals":
        return not (text == goal or (a is not None and b is not None and a == b))
    if test == "contains":
        return goal in text
    return goal not in text  # not_contains


def computed_values(wb, soffice: str = "soffice") -> dict[tuple[str, str], object] | None:
    """What the formulas in the workbook come to, worked out by LibreOffice: {(sheet, cell): value}.
    A workbook written by openpyxl holds formulas but no results until something calculates them.
    None when LibreOffice isn't there or can't open it."""
    import tempfile

    from app.conversion.legacy_office import LegacyOfficeError, convert_with_libreoffice

    with tempfile.TemporaryDirectory(prefix="xlsx-calc-") as tmp:
        work = Path(tmp)
        source = work / "calc.xlsx"
        wb.save(str(source))
        try:
            calculated = convert_with_libreoffice(source, "xlsx", work, soffice)
            data = load_workbook(str(calculated), data_only=True)
        except (LegacyOfficeError, OSError, ValueError):
            return None
        return {
            (ws.title, cell.coordinate): cell.value
            for ws in data.worksheets
            for row in ws.iter_rows()
            for cell in row
            if cell.value is not None
        }


def _values_for_column(wb, ws, rows, col: int, report: Report, op_name: str, letter: str):
    """A function giving a cell's value for sorting or testing. A cell that holds a formula has no value
    until it is calculated, so when the column has formulas LibreOffice works them out first. None (with
    a note) when that isn't possible, so a sort never silently orders by the formula text."""
    if not any(ws.cell(row=r, column=col).data_type == "f" for r in rows):
        return lambda cell: cell.value
    values = computed_values(wb)
    if values is None:
        report.skipped.append(
            f"{op_name}: column {letter} holds formulas, and working out their values needs LibreOffice, which isn't available here"
        )
        return None
    return lambda cell: values.get((ws.title, cell.coordinate)) if cell.data_type == "f" else cell.value


def _other_sheets_have_formulas(wb, ws) -> bool:
    return any(cell.data_type == "f" for other in wb.worksheets if other is not ws for row in other.iter_rows() for cell in row)


def _put(cell, value, report: Report) -> bool:
    """Write a value or a formula. A formula that isn't safe is refused. Returns whether it was written."""
    if isinstance(value, str) and value.startswith("="):
        if not safe_formula(value):
            report.skipped.append(f"{cell.coordinate}: the formula {value[:40]!r} was not allowed")
            return False
    elif isinstance(value, str) and value.strip().startswith("="):
        value = value.strip()
        if not safe_formula(value):
            report.skipped.append(f"{cell.coordinate}: the formula was not allowed")
            return False
    cell.value = value
    report.changed_cells += 1
    return True


def _fill(ws, info: SheetInfo, col: int, op: Op, report: Report, only_empty: bool = False) -> int:
    count = 0
    for r in _data_rows(info):
        cell = ws.cell(row=r, column=col)
        if only_empty and not _is_blank(cell.value):
            continue
        value = op.formula.replace("{row}", str(r)) if op.formula else op.value
        if _put(cell, value, report):
            count += 1
    return count


def _apply(wb, ws, info: SheetInfo, op: Op, report: Report, llm: ParagraphLLM | None) -> None:
    name = f"“{ws.title}”" if len(wb.worksheets) > 1 else "the sheet"

    if op.kind == "set_cell":
        if _put(ws[op.cell], op.value, report):
            report.done.append(f"set {op.cell}")
        return

    if op.kind == "rename_sheet":
        if not op.new_name or any(w.title.lower() == op.new_name.lower() for w in wb.worksheets if w is not ws):
            report.skipped.append(f"rename_sheet: the name “{op.new_name}” can't be used")
        else:
            ws.title = op.new_name
            report.done.append(f"renamed the sheet to “{op.new_name}”")
        return

    if info.last_row == 0:
        report.skipped.append(f"{name} is empty")
        return

    if op.kind == "add_column":
        col = info.last_col + 1
        header_cell = ws.cell(row=info.header_row, column=col)
        header_cell.value = op.header
        if info.last_col:  # look like the other headers
            source = ws.cell(row=info.header_row, column=info.last_col)
            header_cell._style = copy.copy(source._style)
        if op.formula or op.value is not None:
            _fill(ws, info, col, op, report)
        ws.column_dimensions[get_column_letter(col)].width = max(12, len(op.header) + 4)
        report.changed_cells += 1
        report.done.append(f"added the column “{op.header}” ({get_column_letter(col)})")
        info.last_col = col
        return

    col = resolve_column(ws, info, op.column) if op.column else None
    if col is None and (op.column or op.kind != "find_replace"):
        report.skipped.append(f"{op.kind}: there is no column “{op.column}” in {name}")
        return
    letter = get_column_letter(col) if col else ""

    if op.kind == "set_column":
        count = _fill(ws, info, col, op, report)
        report.done.append(f"filled column {letter} ({count} cells)")
    elif op.kind == "transform_column":
        count = 0
        for r in _data_rows(info):
            cell = ws.cell(row=r, column=col)
            value = cell.value
            if cell.data_type == "f" or _is_blank(value):
                continue
            if op.transform == "to_number":
                number = _number(value) if isinstance(value, str) else None
                if number is None:
                    continue
                new = int(number) if number.is_integer() else number
            elif not isinstance(value, str):
                continue
            else:
                new = {"upper": value.upper(), "lower": value.lower(), "title": value.title(), "trim": " ".join(value.split())}[
                    op.transform
                ]
            if new != value:
                cell.value = new
                count += 1
        report.changed_cells += count
        report.done.append(f"{op.transform} on column {letter} ({count} cells changed)")
    elif op.kind == "find_replace":
        flags = 0 if op.match_case else re.IGNORECASE
        pattern = re.compile(re.escape(op.find), flags)
        columns = [col] if col else range(1, info.last_col + 1)
        count = 0
        for c in columns:
            for r in range(1, info.last_row + 1):
                cell = ws.cell(row=r, column=c)
                if isinstance(cell.value, str) and cell.data_type != "f" and pattern.search(cell.value):
                    cell.value = pattern.sub(lambda _m: op.replace, cell.value)
                    count += 1
        report.changed_cells += count
        where = f"column {letter}" if col else name
        report.done.append(f"replaced “{op.find}” with “{op.replace}” in {where} ({count} cells)")
    elif op.kind == "delete_rows_where":
        rows = list(_data_rows(info))
        value_of = _values_for_column(wb, ws, rows, col, report, "delete_rows_where", letter)
        if value_of is None:
            return
        doomed = {r for r in rows if _matches(value_of(ws.cell(row=r, column=col)), op.test, op.value)}
        if doomed:
            _rewrite_rows(ws, info, [r for r in rows if r not in doomed], footer_shift=len(doomed))
            if _other_sheets_have_formulas(wb, ws):
                report.warnings.append("formulas on other sheets that point at the removed rows are not adjusted: check them")
        report.changed_cells += len(doomed)
        report.done.append(f"removed {len(doomed)} row{'s' if len(doomed) != 1 else ''}")
        info.last_row -= len(doomed)
    elif op.kind == "sort":
        rows = list(_data_rows(info))
        if len(rows) > MAX_ROWS_FOR_SORT:
            report.skipped.append(f"sort: too many rows in {name}")
            return

        value_of = _values_for_column(wb, ws, rows, col, report, "sort", letter)
        if value_of is None:
            return

        def key(row):
            v = value_of(ws.cell(row=row, column=col))
            n = _number(v)
            return (0 if n is not None else 1, n if n is not None else 0, _text(v).strip().lower())

        present = [r for r in rows if not _is_blank(value_of(ws.cell(row=r, column=col)))]
        blank = [r for r in rows if _is_blank(value_of(ws.cell(row=r, column=col)))]
        present.sort(key=key, reverse=op.descending)  # blanks always last
        _rewrite_rows(ws, info, present + blank)
        if _other_sheets_have_formulas(wb, ws):
            report.warnings.append("formulas on other sheets that point at the sorted rows are not adjusted: check them")
        report.changed_cells += len(rows)
        report.done.append(f"sorted by column {letter} ({'descending' if op.descending else 'ascending'})")
    elif op.kind == "rename_header":
        ws.cell(row=info.header_row, column=col).value = op.header
        report.changed_cells += 1
        report.done.append(f"renamed the header of column {letter} to “{op.header}”")
    elif op.kind == "format_column":
        for r in _data_rows(info):
            ws.cell(row=r, column=col).number_format = op.number_format
        report.done.append(f"formatted column {letter}")
    elif op.kind == "reword_cells":
        if llm is None:
            report.skipped.append("reword_cells: no model available")
            return
        targets = [
            (r, ws.cell(row=r, column=col).value)
            for r in _data_rows(info)
            if isinstance(ws.cell(row=r, column=col).value, str)
            and ws.cell(row=r, column=col).data_type != "f"
            and ws.cell(row=r, column=col).value.strip()
        ]
        if len(targets) > MAX_REWORD_CELLS:
            report.skipped.append(
                f"reword_cells: column {letter} has {len(targets)} text cells, more than the {MAX_REWORD_CELLS} that can be reworded at once"
            )
            return
        count = 0
        for batch in batches([(r, text) for r, text in targets]):
            for row, new in reword_batch(llm, op.instruction, batch).items():
                if new != ws.cell(row=row, column=col).value:
                    ws.cell(row=row, column=col).value = new
                    count += 1
        report.changed_cells += count
        report.done.append(f"reworded column {letter} ({count} cells)")


def apply_ops(wb, ops: list[Op], llm: ParagraphLLM | None = None) -> Report:
    report = Report()
    for op in ops:
        ws = wb[op.sheet] if op.sheet in wb.sheetnames else None
        if ws is None:  # renamed by an earlier operation
            report.skipped.append(f"{op.kind}: the sheet “{op.sheet}” was renamed")
            continue
        try:
            _apply(wb, ws, sheet_info(ws), op, report, llm)
        except Exception as exc:  # one bad operation must not lose the others
            report.skipped.append(f"{op.kind} failed: {exc}")
    return report


# ---------- the whole edit ----------


@dataclass(frozen=True)
class XlsxResult:
    changed: int
    summary: str
    skipped: tuple = ()
    warnings: tuple = ()


def _loses_something(src: Path) -> list[str]:
    """What openpyxl cannot keep from this file, in words (so the reply can say so)."""
    import zipfile

    try:
        names = zipfile.ZipFile(src).namelist()
    except zipfile.BadZipFile:
        return []
    lost = []
    if any(n.startswith("xl/charts/") for n in names):
        lost.append("charts")
    if any(n.startswith("xl/pivot") for n in names):
        lost.append("pivot tables")
    if any(n.startswith("xl/media/") for n in names):
        lost.append("pictures")
    return lost


def edit_xlsx(src: Path, dst: Path, instruction: str, llm: ParagraphLLM) -> Iterator[EditProgress | XlsxResult]:
    if src.stat().st_size > MAX_FILE_BYTES:
        raise LayoutUnsupported(f"it is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    try:
        wb = load_workbook(str(src))
    except Exception as exc:
        raise LayoutUnsupported(f"it couldn't be opened as an Excel file ({exc})") from exc

    listing = describe_workbook(wb)
    if not listing or all(sheet_info(ws).last_row == 0 for ws in wb.worksheets):
        raise LayoutUnsupported("the workbook is empty")
    if len(listing) > MAX_PROMPT_CHARS:
        listing = listing[:MAX_PROMPT_CHARS] + "\n(cut)"

    yield EditProgress("Working out exactly what to change")
    data = chat_json(llm, OPS_SYSTEM_PROMPT, f"Request:\n{instruction}\n\nWorkbook:\n{listing}", temperature=0.1)
    ops, dropped = parse_ops(data, wb)
    if not ops:
        problem = data.get("problem") if isinstance(data.get("problem"), str) else ""
        raise ValueError(problem or (dropped[0] if dropped else "") or "I couldn't tell what to change in the file for that request")

    yield EditProgress(f"Making {len(ops)} change{'s' if len(ops) != 1 else ''}")
    report = apply_ops(wb, ops, llm)
    if not report.done:
        raise ValueError(report.skipped[0] if report.skipped else "nothing in the file needed to change for that request")

    lost = _loses_something(src)
    wb.save(str(dst))
    warnings = list(report.warnings)
    if lost:
        warnings.append(f"the original had {' and '.join(lost)}, which this kind of edit does not keep")
    yield XlsxResult(
        changed=report.changed_cells,
        summary="; ".join(report.done),
        skipped=tuple((dropped + report.skipped)[:6]),
        warnings=tuple(warnings),
    )

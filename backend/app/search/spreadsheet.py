"""Build a spreadsheet from the documents in scope: "list every invoice with its date, vendor and
total" becomes an .xlsx with one row per invoice and a column for the source file.

Two model steps, both asked for JSON:

  1. plan   -- from the request: which columns, and is a row one per document ("each CV: name,
               email") or one per thing found inside them ("every invoice", "all deadlines")
  2. extract -- each document is read in pieces (like a full report), in parallel, and the model
               returns the rows it finds in that piece, using only what the text says

Code does the rest: lines up the columns, joins the pieces, adds the source file name, caps the
size and writes the workbook (app/export/tables.py). A cell the documents do not answer stays empty.
Text that comes from the documents is never written as a formula (tables.py).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from app.editing.formats import safe_stem
from app.editing.layout import chat_json
from app.export.tables import Table, write_xlsx
from app.search.report import (
    NO_DOCUMENTS_MESSAGE,
    LLMClient,
    ReportDocument,
    ReportLimits,
    _nothing_to_report,
    _status,
    batch_sections,
)
from app.search.query import StreamEvent

logger = logging.getLogger(__name__)

MAX_COLUMNS = 20
MAX_ROWS = 5000
MAX_ROWS_PER_PIECE = 200
PREVIEW_ROWS = 10
SOURCE_COLUMN = "Source"

PLAN_SYSTEM_PROMPT = (
    "You design a spreadsheet. You get a request that says what to collect from a person's documents. "
    "Reply with JSON only:\n"
    '{"sheet": "short sheet title", "one_row_per": "document" or "item", '
    '"columns": [{"name": "Column name", "hint": "what goes in it, in a few words"}]}\n'
    "Rules:\n"
    "- If the request names the columns, use exactly those, in that order, with the names as written. "
    "Otherwise choose the few columns the request clearly needs (at most 12).\n"
    '- "one_row_per": "document" when each document gives one row ("for each CV: name and email"); '
    '"item" when a document can hold several of the things asked for ("every invoice", "all deadlines", '
    '"each action item").\n'
    "- Do not add a column for the file name: it is added automatically.\n"
    '- If the request does not say what to collect, reply {"columns": []}.'
)

EXTRACT_SYSTEM_PROMPT = (
    "You fill a spreadsheet from a document. You get the columns, whether a row is one per document or one "
    "per item found in it, the request, and part of a document. Reply with JSON only: "
    '{"rows": [{"<column name>": value, ...}]}\n'
    "Rules:\n"
    "- Use only what the text says. Never guess or invent: when the text does not give a value, use an empty "
    'string "".\n'
    "- Write numbers as plain numbers without currency symbols or thousands separators (1200.50), and "
    "dates as YYYY-MM-DD when the date is clear. Keep other text as written, in the document's language.\n"
    "- One row per item found in this part of the document (or a single row for the document when the "
    "rows are one per document).\n"
    '- If this part has nothing that fits the request, reply {"rows": []}.'
)

NO_COLUMNS_MESSAGE = (
    "I couldn't tell what the spreadsheet should contain. Say what each row should have, for example "
    "“a spreadsheet of every invoice with invoice number, date, vendor and total”."
)
TOO_MANY_MESSAGE = (
    "A spreadsheet reads every selected document, and this selection is too large for that ({detail}). "
    "Attach just the files you want, or choose a smaller folder under Scope, and ask again."
)


@dataclass(frozen=True)
class Column:
    name: str
    hint: str = ""


@dataclass(frozen=True)
class SheetPlan:
    sheet: str
    one_row_per: str
    columns: list[Column]


@dataclass
class DocumentRows:
    name: str
    rows: list[dict] = field(default_factory=list)
    pieces: int = 0
    failed_pieces: int = 0


# ---------- the plan ----------


def parse_plan(data: dict) -> SheetPlan | None:
    """The columns the model chose, cleaned (unique names, at most MAX_COLUMNS), or None when it chose none."""
    columns: list[Column] = []
    seen = {SOURCE_COLUMN.lower()}
    raw_columns = data.get("columns")
    for raw in raw_columns if isinstance(raw_columns, list) else []:
        if isinstance(raw, str):
            name, hint = raw, ""
        elif isinstance(raw, dict) and isinstance(raw.get("name"), str):
            name, hint = raw["name"], raw.get("hint") if isinstance(raw.get("hint"), str) else ""
        else:
            continue
        name = " ".join(name.split())[:60]
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        columns.append(Column(name, hint.strip()[:120]))
        if len(columns) == MAX_COLUMNS:
            break
    if not columns:
        return None
    sheet = data.get("sheet") if isinstance(data.get("sheet"), str) else ""
    one_row_per = "document" if str(data.get("one_row_per", "")).strip().lower() == "document" else "item"
    return SheetPlan(sheet=" ".join(sheet.split())[:31] or "Data", one_row_per=one_row_per, columns=columns)


# ---------- reading rows out of a reply ----------


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (list, tuple)):
        return "; ".join(_cell_text(v) for v in value if _cell_text(v))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def parse_rows(data: dict, columns: list[Column]) -> list[dict]:
    """The rows in the model's reply as {column name: text}. Keys are matched to the columns ignoring
    case and spacing; unknown keys are dropped; a row with every cell empty is dropped."""
    by_key = {re.sub(r"\s+", " ", c.name).strip().lower(): c.name for c in columns}
    rows: list[dict] = []
    raw_rows = data.get("rows")
    for raw in (raw_rows if isinstance(raw_rows, list) else [])[:MAX_ROWS_PER_PIECE]:
        if not isinstance(raw, dict):
            continue
        row = {name: "" for name in by_key.values()}
        for key, value in raw.items():
            name = by_key.get(re.sub(r"\s+", " ", str(key)).strip().lower())
            if name:
                row[name] = _cell_text(value)
        if any(row.values()):
            rows.append(row)
    return rows


def merge_document_rows(rows: list[dict], columns: list[Column]) -> list[dict]:
    """For "one row per document": the pieces of a long document each give a partial row; the first
    non-empty value of every column wins, so the document ends up as one row."""
    if not rows:
        return []
    merged = {c.name: "" for c in columns}
    for row in rows:
        for name in merged:
            if not merged[name] and row.get(name):
                merged[name] = row[name]
    return [merged]


# ---------- the table ----------


def build_table(plan: SheetPlan, results: list[DocumentRows]) -> tuple[Table, bool]:
    """The workbook's table (Source first, then the planned columns) and whether it was cut at MAX_ROWS."""
    headers = [SOURCE_COLUMN] + [c.name for c in plan.columns]
    rows: list[list[str]] = []
    truncated = False
    for result in results:
        for row in result.rows:
            if len(rows) >= MAX_ROWS:
                truncated = True
                break
            rows.append([result.name] + [row.get(c.name, "") for c in plan.columns])
    return Table(headers=headers, rows=rows, title=plan.sheet), truncated


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def preview_markdown(table: Table, limit: int = PREVIEW_ROWS) -> str:
    lines = [
        "| " + " | ".join(_escape_cell(h) for h in table.headers) + " |",
        "| " + " | ".join("---" for _ in table.headers) + " |",
    ]
    for row in table.rows[:limit]:
        lines.append("| " + " | ".join(_escape_cell(c) for c in row) + " |")
    return "\n".join(lines)


# ---------- the run ----------

Publish = Callable[[Path, str], dict]


def _extract_user(request: str, plan: SheetPlan, document: ReportDocument, piece: str) -> str:
    columns = "\n".join(f"- {c.name}" + (f": {c.hint}" if c.hint else "") for c in plan.columns)
    return (
        f"Columns:\n{columns}\n\nRows: one per {plan.one_row_per}\n\nRequest: {request}\n\n"
        f"Document: {document.name}\n\n{piece}"
    )


def spreadsheet_events(
    request: str,
    documents: list[ReportDocument],
    llm: LLMClient,
    limits: ReportLimits,
    out_dir: Path,
    publish: Publish,
) -> Iterator[StreamEvent]:
    """Events for the chat: status lines while it works, a `file` event for the workbook, then the
    reply (a short summary and a preview of the first rows)."""
    yield {"event": "meta", "data": {"sources": [d.path for d in documents], "grounded": True}}

    yield _status("Working out the columns…")
    try:
        plan = parse_plan(chat_json(llm, PLAN_SYSTEM_PROMPT, f"Request: {request}", temperature=0.1))
    except Exception as exc:
        logger.warning("planning the spreadsheet failed", exc_info=True)
        yield from _message(f"I couldn't work out the columns for that request ({exc}). Try listing them, for example “invoice number, date and total”.")
        return
    if plan is None:
        yield from _message(NO_COLUMNS_MESSAGE)
        return

    pieces: list[tuple[int, str]] = []
    results = [DocumentRows(name=d.name) for d in documents]
    for index, document in enumerate(documents):
        batches = batch_sections(document.sections, limits.batch_chars)
        results[index].pieces = len(batches)
        pieces.extend((index, batch) for batch in batches)

    column_list = ", ".join(c.name for c in plan.columns)
    yield _status(f"Reading {len(documents)} document{'s' if len(documents) != 1 else ''} for: {column_list}")

    found: dict[int, list[dict]] = {index: [] for index in range(len(documents))}

    def read(piece: tuple[int, str]) -> tuple[int, list[dict] | None]:
        index, text = piece
        try:
            data = chat_json(llm, EXTRACT_SYSTEM_PROMPT, _extract_user(request, plan, documents[index], text), temperature=0.1)
        except Exception:
            logger.warning("reading %s for the spreadsheet failed", documents[index].name, exc_info=True)
            return index, None
        return index, parse_rows(data, plan.columns)

    executor = ThreadPoolExecutor(max_workers=max(1, min(limits.parallel, len(pieces) or 1)))
    try:
        futures = [executor.submit(read, piece) for piece in pieces]
        for done, future in enumerate(as_completed(futures), start=1):
            index, rows = future.result()
            if rows is None:
                results[index].failed_pieces += 1
            else:
                found[index].extend(rows)
            yield _status(f"Reading your documents… {done} of {len(pieces)} parts done ({documents[index].name})")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)  # a stopped run must not keep starting reads

    for index, result in enumerate(results):
        result.rows = merge_document_rows(found[index], plan.columns) if plan.one_row_per == "document" else found[index]

    table, truncated = build_table(plan, results)
    failed = [r for r in results if r.failed_pieces]
    if not table.rows:
        message = f"I read {len(documents)} document{'s' if len(documents) != 1 else ''} and found nothing that fits this request."
        if failed:
            message += f" {_failed_note(failed)}"
        yield from _message(message)
        return

    yield _status("Writing the spreadsheet…")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = write_xlsx([table], out_dir / f"{safe_stem(plan.sheet.lower()) or 'spreadsheet'}.xlsx")
    source = f"{len(documents)} document{'s' if len(documents) != 1 else ''}"
    yield {"event": "file", "data": publish(path, source)}

    with_rows = sum(1 for r in results if r.rows)
    shown = min(len(table.rows), PREVIEW_ROWS)
    lines = [
        f"I built **{path.name}** from {with_rows} of {len(documents)} document{'s' if len(documents) != 1 else ''}: "
        f"{len(table.rows)} row{'s' if len(table.rows) != 1 else ''} and {len(table.headers)} columns "
        f"({SOURCE_COLUMN}, {column_list}).",
        "",
        preview_markdown(table),
    ]
    if len(table.rows) > shown:
        lines += ["", f"Showing the first {shown} of {len(table.rows)} rows; the file has them all."]
    notes = ["A cell the documents don't answer is left empty."]
    empty = [r.name for r in results if not r.rows and not r.failed_pieces]
    if empty:
        notes.append(f"Nothing found in: {', '.join(empty[:8])}{' and more' if len(empty) > 8 else ''}.")
    if failed:
        notes.append(_failed_note(failed))
    if truncated:
        notes.append(f"The file stops at {MAX_ROWS:,} rows.")
    lines += ["", " ".join(notes)]
    yield from _message("\n".join(lines))


def _failed_note(failed: list[DocumentRows]) -> str:
    names = ", ".join(r.name for r in failed[:6]) + (" and more" if len(failed) > 6 else "")
    return f"Some parts couldn't be read ({names}), so those documents may be incomplete."


def _message(text: str) -> Iterator[StreamEvent]:
    """The reply text, after `meta` has already said which documents were read."""
    yield {"event": "token", "data": {"text": text}}
    yield {"event": "extra", "data": {"cross_doc": None, "statistical": None}}


# ---------- the endpoint's entry ----------


def sheet_scope_problem(documents: list[ReportDocument], limits: ReportLimits) -> str | None:
    if not documents:
        return NO_DOCUMENTS_MESSAGE
    total = sum(d.chars for d in documents)
    if len(documents) > limits.max_files:
        return TOO_MANY_MESSAGE.format(detail=f"{len(documents)} documents, the limit is {limits.max_files}")
    if total > limits.max_chars:
        return TOO_MANY_MESSAGE.format(detail=f"about {total // 1000}k characters, the limit is {limits.max_chars // 1000}k")
    return None


def spreadsheet_stream(session, payload) -> Iterator[StreamEvent]:
    """The dashboard's Spreadsheet toggle: every document in the request's scope is read for the rows the
    message asks for, and the result is a new .xlsx (recorded in export history like any generated file)."""
    from app.config import settings
    from app.history.store import record_export
    from app.search.report import load_report_documents
    from app.search.report_runner import report_limits
    from app.tasks.correlate import _raw_dir, _text_llm

    def publish(path: Path, source: str) -> dict:
        export_id = record_export(
            session, query_history_id=None, filename=path.stem, fmt="xlsx", stored_path=path, source_name=source
        )
        return {
            "id": export_id,
            "name": path.name,
            "fmt": "xlsx",
            "size_bytes": path.stat().st_size,
            "source": source,
            "download_path": f"/history/exports/{export_id}/download",
        }

    if payload.chat_only:  # a direct chat has no documents to read
        yield from _nothing_to_report(NO_DOCUMENTS_MESSAGE)
        return
    documents = load_report_documents(session, _raw_dir, payload.folders, payload.author, payload.title, payload.file_ids)
    limits = report_limits()
    problem = sheet_scope_problem(documents, limits)
    if problem:
        yield from _nothing_to_report(problem)
        return
    out_dir = Path(settings.data_dir) / "outputs" / uuid.uuid4().hex
    yield from spreadsheet_events(payload.question, documents, _text_llm, limits, out_dir, publish)

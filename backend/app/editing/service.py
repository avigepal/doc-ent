"""Run an edit request end to end for the chat: find the attached files, edit
each with the model, save the result as a new file, and report it.

Yields the same event dicts as app/search/query.py:stream_query (meta,
token, extra) plus two more the dashboard understands:
  status -- a progress line while the model works
  file   -- a finished file (its export-history id doubles as the download id)

The original upload is never touched; the new file goes to
DATA_DIR/outputs/<random>/<name>-edited.<ext> and is recorded in export
history, so it also shows up on the History page.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.conversion.backends import clean_converted_markdown
from app.editing.engine import (
    DocumentTooLargeError,
    EditLLM,
    EditProgress,
    EditResult,
    edit_document,
)
from app.editing.changes import describe_changes, save_changes
from app.editing.docx_layout import edit_docx
from app.editing.docx_ops import edit_docx_exact, wants_exact_edit
from app.editing.docx_style import StyleResult, restyle_docx
from app.editing.formats import (
    EditNotSupportedError,
    is_structured,
    output_filename,
    output_format,
    requested_format,
    validate_output,
    write_output,
)
from app.editing.layout import LayoutResult, LayoutUnsupported, needs_rebuild
from app.editing.literal import parse_replacement
from app.editing.pdf_layout import edit_pdf
from app.editing.request import split_request
from app.editing.styling import mentions_style
from app.handlers.registry import is_plain_text
from app.history.store import edits_in_conversation, record_export
from app.models import FileRecord

logger = logging.getLogger(__name__)

MAX_FILES_PER_EDIT = 5

# Docling leaves "<!-- image -->" where a picture was; it's noise in an edit.
_IMAGE_PLACEHOLDER = re.compile(r"<!--\s*image\s*-->\n*")

# The edited text is kept next to each result, so the next edit in the chat
# can start from it (the PDF/Word file itself can't be read back faithfully).
EDITED_TEXT_NAME = "edited_text.md"

# Present next to results that were edited in the original layout (there is no
# separate text to continue from: the file itself is the thing to edit next).
LAYOUT_FLAG_NAME = "layout.flag"

# "...from the original", "start over": don't continue from the previous result
_WANTS_ORIGINAL = re.compile(
    r"\b(?:from\s+(?:the\s+)?(?:original|scratch|beginning)|(?:the\s+)?original\s+(?:file|version|document|upload)|"
    r"start\s+(?:over|again|fresh)|redo\s+(?:it\s+)?from)\b",
    re.IGNORECASE,
)

NEED_FILE_MESSAGE = (
    "To edit a file, attach it first with the upload button next to the message box, "
    "then tell me what to change."
)


def load_source_text(file_record: FileRecord, data_dir: str) -> str:
    """The text of an uploaded file: the file itself if it is text, otherwise
    the Markdown it was converted to when it was ingested."""
    path = Path(file_record.path)
    if is_plain_text(file_record.mime_type, path.name):
        return path.read_text(encoding="utf-8", errors="replace")

    converted = (Path(data_dir) / "converted" / path.relative_to(Path(data_dir) / "raw")).with_suffix(".md")
    if not converted.exists():
        raise FileNotFoundError("it hasn't finished processing yet, try again in a moment")
    text = converted.read_text(encoding="utf-8", errors="replace")
    # files converted before the cleanup existed still carry the junk
    return clean_converted_markdown(_IMAGE_PLACEHOLDER.sub("", text))


def wants_original(instruction: str) -> bool:
    return bool(_WANTS_ORIGINAL.search(instruction))


def _previous_result(edits: list) -> tuple[object, Path] | None:
    """The newest earlier edit whose text is still on disk."""
    for record in edits:
        sidecar = Path(record.stored_path).parent / EDITED_TEXT_NAME
        if sidecar.exists():
            return record, sidecar
    return None


@dataclass(frozen=True)
class Previous:
    record: object
    kind: str  # "text": continue from the saved text; "layout": edit the file itself
    path: Path


def _previous(edits: list) -> Previous | None:
    """The newest earlier edit that can be continued from."""
    for record in edits:
        folder = Path(record.stored_path).parent
        if (folder / EDITED_TEXT_NAME).exists():
            return Previous(record, "text", folder / EDITED_TEXT_NAME)
        if (folder / LAYOUT_FLAG_NAME).exists() and Path(record.stored_path).exists():
            return Previous(record, "layout", Path(record.stored_path))
    return None


def file_to_text(path: Path, fmt: str) -> str:
    """The text of a file this app made, to continue editing it as a clean
    document after the layout-keeping route had to be given up."""
    if fmt == "pdf":
        import pymupdf

        with pymupdf.open(str(path)) as doc:
            return "\n\n".join(page.get_text("text").strip() for page in doc).strip()
    if fmt == "docx":
        run = subprocess.run(["pandoc", str(path), "-f", "docx", "-t", "gfm", "--wrap=none"], capture_output=True)
        if run.returncode != 0:
            raise RuntimeError("couldn't read the previous result")
        return run.stdout.decode("utf-8", errors="replace")
    return path.read_text(encoding="utf-8", errors="replace")


def _publish(session: Session, path: Path, fmt: str, shown: str, source_file_id: int) -> dict:
    """Record a finished file in export history and build the event that
    shows its card in the chat."""
    export_id = record_export(
        session,
        query_history_id=None,
        filename=path.stem,
        fmt=fmt,
        stored_path=path,
        source_name=shown,
        source_file_id=source_file_id,
    )
    data = {
        "id": export_id,
        "name": path.name,
        "fmt": fmt,
        "size_bytes": path.stat().st_size,
        "source": shown,
        "download_path": f"/history/exports/{export_id}/download",
    }
    changes = describe_changes(path) if fmt == "docx" else None
    if changes:
        data["changes"] = changes  # the dashboard lets the user accept or reject each one
    return {"event": "file", "data": data}


def _restyle_in_layout(
    session: Session,
    *,
    row: FileRecord,
    instruction: str,
    llm: EditLLM,
    data_dir: str,
    base: Path,
    shown: str,
    version: int,
    chained: bool,
) -> Iterator[dict]:
    """Change how a Word file looks (fonts, colours, spacing, margins...) and
    leave its text alone. Returns the note for the reply."""
    name = Path(row.path).name
    out_dir = Path(data_dir) / "outputs" / uuid.uuid4().hex
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / output_filename(Path(name).stem, "docx", version)

    result: StyleResult | None = None
    try:
        for update in restyle_docx(base, dst, instruction, llm):
            if isinstance(update, StyleResult):
                result = update
            else:
                yield _status(f"{name}: {update.text}")
        assert result is not None
        (out_dir / LAYOUT_FLAG_NAME).write_text("1", encoding="utf-8")
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise

    yield _status(f"Saving {name}…")
    yield _publish(session, dst, "docx", shown, row.id)

    note = f"Changed the style of **{shown}** ({result.summary}) and saved the result as **{dst.name}**. The text is unchanged."
    if result.skipped:
        note += f" The file has no {', no '.join(result.skipped)}, so that part was skipped."
    if result.ignored:
        note += f" I couldn't apply: {', '.join(sorted(set(result.ignored)))}."
    if chained:
        note += " This continues from your previous edit; say \u201cfrom the original\u201d to start over."
    return note


def _style_note(result: StyleResult) -> str:
    note = f"Changed the style ({result.summary})."
    if result.skipped:
        note += f" The file has no {', no '.join(result.skipped)}, so that part was skipped."
    if result.ignored:
        note += f" I couldn't apply: {', '.join(sorted(set(result.ignored)))}."
    return note


def _restyle_output(path: Path, fmt: str, style: str, llm: EditLLM, name: str) -> Iterator[dict]:
    """The look-part of a request that also changed the words: restyle the file
    just written (replaced in place, so there is still one result). Returns
    (the path, a sentence for the reply, whether the style was applied). A style
    that can't be applied never loses the wording change that was already made."""
    if fmt != "docx":
        return path, (
            f" I can only change the styling of Word (.docx) files, so I left the look as it was ({style})."
        ), False, []
    tmp = path.with_name(f"{path.stem}.styling.docx")
    result: StyleResult | None = None
    try:
        for update in restyle_docx(path, tmp, style, llm):
            if isinstance(update, StyleResult):
                result = update
            else:
                yield _status(f"{name}: {update.text}")
        assert result is not None
        tmp.replace(path)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        logger.warning("styling %s after the edit failed", name, exc_info=True)
        return path, f" I couldn't apply the style changes: {exc}.", False, []
    return path, " " + _style_note(result), True, list(result.ops)


def _edit_in_layout(
    session: Session,
    *,
    row: FileRecord,
    fmt: str,
    instruction: str,
    llm: EditLLM,
    data_dir: str,
    base: Path,
    shown: str,
    version: int,
    chained: bool,
    style: str = "",
    exact: bool = False,
) -> Iterator[dict]:
    """Edit `base` (a .docx or .pdf) in place. Yields status/file events and
    returns the note for the reply; raises LayoutUnsupported when the file
    or the change can't be done this way (the caller then rebuilds it)."""
    name = Path(row.path).name
    out_dir = Path(data_dir) / "outputs" / uuid.uuid4().hex
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / output_filename(Path(name).stem, fmt, version)
    editor = edit_docx_exact if exact else edit_docx if fmt == "docx" else edit_pdf

    result: LayoutResult | None = None
    try:
        for update in editor(base, dst, instruction, llm):
            if isinstance(update, LayoutResult):
                result = update
            else:
                yield _status(f"{name}: {update.text}")
        assert result is not None
        (out_dir / LAYOUT_FLAG_NAME).write_text("1", encoding="utf-8")
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise

    style_note = ""
    style_ops: list = []
    if style:
        dst, style_note, _, style_ops = yield from _restyle_output(dst, fmt, style, llm, name)
    if fmt == "docx" and result.changes:
        # so each change can be accepted or rejected later (changes.py)
        save_changes(dst, base, list(result.ops), list(result.changes), style_ops)

    yield _status(f"Saving {name}…")
    yield _publish(session, dst, fmt, shown, row.id)

    if result.summary:
        note = f"Edited **{shown}**: {result.summary}, keeping its layout, "
    elif result.replaced:
        times = "once" if result.replacements == 1 else f"{result.replacements} times"
        note = f'Replaced "{result.replaced.old}" with "{result.replaced.new}" in **{shown}** ({times}), keeping its layout, '
    else:
        count = "one paragraph" if result.changed == 1 else f"{result.changed} paragraphs"
        note = f"Applied your changes to **{shown}** ({count}), keeping its layout, "
    note += f"and saved the result as **{dst.name}**.{style_note}"
    if result.skipped:
        note += f" Not done: {'; '.join(result.skipped)}."
    if chained:
        note += " This continues from your previous edit; say “from the original” to start over."
    return note


def _status(text: str) -> dict:
    return {"event": "status", "data": {"text": text}}


def _finish(message: str) -> Iterator[dict]:
    yield {"event": "token", "data": {"text": message}}
    yield {"event": "extra", "data": {"cross_doc": None, "statistical": None}}


def need_file_events() -> Iterator[dict]:
    yield {"event": "meta", "data": {"sources": [], "grounded": False}}
    yield from _finish(NEED_FILE_MESSAGE)


def edit_events(
    session: Session,
    *,
    instruction: str,
    file_ids: list[int],
    llm: EditLLM,
    data_dir: str | None = None,
    conversation_id: str = "",
    before_history_id: int | None = None,
) -> Iterator[dict]:
    """`conversation_id` lets an edit continue from the previous result in the
    same chat; `before_history_id` (set when regenerating an earlier reply)
    limits that to what came before it."""
    data_dir = data_dir or settings.data_dir

    rows = list(
        session.execute(select(FileRecord).where(FileRecord.id.in_(file_ids)).order_by(FileRecord.id)).scalars()
    )[:MAX_FILES_PER_EDIT]
    if not rows:
        yield {"event": "meta", "data": {"sources": [], "grounded": False}}
        yield from _finish(NEED_FILE_MESSAGE)
        return

    yield {"event": "meta", "data": {"sources": [r.path for r in rows], "grounded": True}}

    notes: list[str] = []
    for row in rows:
        name = Path(row.path).name
        yield _status(f"Reading {name}…")
        try:
            earlier = edits_in_conversation(session, conversation_id, row.id, before_history_id)
            previous = None if wants_original(instruction) else _previous(earlier)
            requested = requested_format(instruction) or (previous.record.fmt if previous else None)
            fmt = output_format(name, row.mime_type, requested)
            version = len(earlier) + 1
            shown = Path(previous.record.stored_path).name if previous else name

            # A request can change the words, the look, or both ("shorten the intro and make the
            # headings dark blue"): split it, change the words first, then restyle the result.
            text_instruction, style_instruction = instruction, ""
            if parse_replacement(instruction) is None and mentions_style(instruction):
                yield _status(f"{name}: Working out what to change…")
                split = split_request(llm, instruction)
                text_instruction, style_instruction = split.text, split.style

            # "make the headings blue", "use Arial": the look changes, the words don't.
            if style_instruction and not text_instruction:
                docx_base = None
                if previous is not None and previous.record.fmt == "docx" and Path(previous.record.stored_path).exists():
                    docx_base = Path(previous.record.stored_path)
                elif previous is None and Path(row.path).suffix.lower() == ".docx":
                    docx_base = Path(row.path)
                if docx_base is None:
                    kind = Path(previous.record.stored_path).suffix.lstrip(".").upper() if previous else Path(row.path).suffix.lstrip(".").upper()
                    notes.append(
                        f"I can change the styling of Word (.docx) files. **{shown}** is a {kind or 'plain'} file, "
                        "so I can only change its words. Upload the Word version to restyle it."
                    )
                    continue
                note = yield from _restyle_in_layout(
                    session,
                    row=row,
                    instruction=style_instruction,
                    llm=llm,
                    data_dir=data_dir,
                    base=docx_base,
                    shown=shown,
                    version=version,
                    chained=previous is not None,
                )
                notes.append(note)
                continue

            # Word and PDF files are edited in place, keeping their layout, when the
            # change is a replacement or a rewording of the existing text.
            layout_note = None
            native = {".docx": "docx", ".pdf": "pdf"}.get(Path(row.path).suffix.lower())
            if native and fmt == native and (previous is None or previous.kind == "layout"):
                base = previous.path if previous else Path(row.path)
                structural = needs_rebuild(text_instruction)
                # Word: add, remove or change named parts exactly (docx_ops.py) instead of rewording every paragraph
                exact = (
                    fmt == "docx"
                    and parse_replacement(instruction) is None
                    and wants_exact_edit(text_instruction, structural)
                )
                if parse_replacement(instruction) is None and structural and not exact:
                    layout_note = (
                        "This change adds, removes or rearranges content, which can't be done inside the "
                        "original layout, so the result is a clean document."
                    )
                elif base.exists():
                    try:
                        note = yield from _edit_in_layout(
                            session,
                            row=row,
                            fmt=fmt,
                            instruction=text_instruction,
                            llm=llm,
                            data_dir=data_dir,
                            base=base,
                            shown=shown,
                            version=version,
                            chained=previous is not None,
                            style=style_instruction,
                            exact=exact,
                        )
                        notes.append(note)
                        continue
                    except LayoutUnsupported as exc:
                        layout_note = f"I couldn't keep the original layout ({exc}), so the result is a clean document."

            if previous and previous.kind == "text":
                text = previous.path.read_text(encoding="utf-8")
            elif previous:
                text = file_to_text(previous.path, previous.record.fmt)
            else:
                text = load_source_text(row, data_dir)

            result: EditResult | None = None
            for update in edit_document(text, text_instruction, llm, structured=is_structured(fmt)):
                if isinstance(update, EditProgress):
                    yield _status(f"{name}: {update.text}")
                else:
                    result = update
            assert result is not None

            yield _status(f"Saving {name}…")
            out_dir = Path(data_dir) / "outputs" / uuid.uuid4().hex
            path = write_output(result.text, fmt, out_dir, Path(name).stem, version=version)
            (out_dir / EDITED_TEXT_NAME).write_text(result.text, encoding="utf-8")
            style_note = ""
            if style_instruction:
                path, style_note, styled, _ = yield from _restyle_output(path, fmt, style_instruction, llm, name)
                if styled:
                    # the file now looks different from its text: the next edit continues from the file itself
                    (out_dir / EDITED_TEXT_NAME).unlink(missing_ok=True)
                    (out_dir / LAYOUT_FLAG_NAME).write_text("1", encoding="utf-8")
            export_id = record_export(
                session,
                query_history_id=None,
                filename=path.stem,
                fmt=fmt,
                stored_path=path,
                source_name=shown,
                source_file_id=row.id,
            )
            yield {
                "event": "file",
                "data": {
                    "id": export_id,
                    "name": path.name,
                    "fmt": fmt,
                    "size_bytes": path.stat().st_size,
                    "source": shown,
                    "download_path": f"/history/exports/{export_id}/download",
                },
            }

            if result.replaced:
                times = "once" if result.replacements == 1 else f"{result.replacements} times"
                note = (
                    f'Replaced "{result.replaced.old}" with "{result.replaced.new}" in **{shown}** '
                    f"({times}) and saved the result as **{path.name}**."
                )
            else:
                note = f"Applied your changes to **{shown}** and saved the result as **{path.name}**."
            note += style_note
            if layout_note:
                note += f" {layout_note}"
            if previous:
                note += " This continues from your previous edit; say “from the original” to start over."
            if not result.single_pass:
                note += (
                    " The file was long, so it was edited section by section; changes that depend on"
                    " the whole document may be incomplete."
                )
            warning = validate_output(result.text, fmt)
            if warning:
                note += f" Note: {warning}."
            notes.append(note)
        except (EditNotSupportedError, DocumentTooLargeError, FileNotFoundError, ValueError) as exc:
            notes.append(f"I couldn't edit **{name}**: {exc}.")
        except Exception as exc:
            logger.warning("editing %s failed", name, exc_info=True)
            notes.append(f"I couldn't edit **{name}**: {exc}.")

    yield from _finish("\n\n".join(notes))

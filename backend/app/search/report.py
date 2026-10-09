"""Full report: read EVERY passage of the files in scope and write a complete
report from all of it.

A normal question is answered from the few passages that match best, which is
right for "what is X?" but cannot give "everything about this person" -- the
details sit in passages that do not match the words of the request. Here the
documents are read in full, in stages:

  1. map     -- each document is read in batches; the model notes down every
                fact relevant to the request (names, dates, numbers...).
  2. condense-- if the notes together are too long for one prompt, the longest
                ones are shortened (keeping every fact).
  3. reduce  -- the report is written from the notes, citing the documents,
                streamed like any other answer.

Events have the same shape as app/search/query.py:stream_query, plus "status"
events so the dashboard can show progress while the documents are read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChunkRecord, FileRecord
from app.search.pgvector_retrieval import file_filter_conditions
from app.search.query import StreamEvent, _stream_answer

# Reading is note-taking, not reasoning: switch a reasoning model's thinking off
# so each batch costs seconds, not a thinking session.
_NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}

NOTES_MAX_TOKENS = 1500

MAP_SYSTEM_PROMPT = (
    "You take notes from ONE document for a report.\n"
    "Use only the text you are given. List every fact that is relevant to the request as short bullet "
    "points, keeping exact names, dates, numbers, places, titles, contact details and quotes. "
    "Do not add anything that is not in the text and do not interpret. "
    "If the text has nothing relevant to the request, reply with exactly: NONE"
)

CONDENSE_SYSTEM_PROMPT = (
    "Shorten these notes without losing any fact. Keep every name, date, number, place, title and detail; "
    "remove repetition and wording only. Reply with the shortened bullet points and nothing else."
)

REPORT_SYSTEM_PROMPT = (
    "You write a complete report for the user from notes taken from their documents.\n"
    "\n"
    "Rules:\n"
    "1. Use ONLY the notes below. No outside knowledge, no guessing; if something is not in the notes, "
    "it does not exist for you.\n"
    "2. Cite the document a fact comes from as [1] or [1][2] at the end of the sentence or bullet.\n"
    "3. Be thorough: include every relevant fact from the notes, not just the main ones.\n"
    "4. If documents disagree (different dates, spellings, figures), say so and cite both.\n"
    "5. End with a short \"Not covered\" list of what the documents do not say about the request "
    "(only things a reader would expect to find).\n"
    "\n"
    "Format: Markdown. Open with a two-sentence overview, then ### sections with headings that fit the "
    "material (for a person, for example: background, education, work history, skills, contact details, "
    "other notable details). Use bullets for lists of facts and a table where it compares several items."
)

NO_DOCUMENTS_MESSAGE = (
    "There are no readable documents to build a report from. Attach the files to this chat, or choose a "
    "folder under Scope, and wait until they show as ready."
)
NOTHING_RELEVANT_MESSAGE = (
    "I read all {count} of your documents and found nothing relevant to this request."
)
TOO_MANY_MESSAGE = (
    "A full report reads every selected document, and this selection is too large for that "
    "({detail}). Attach just the files you want covered, or choose a smaller folder under Scope, "
    "and ask again."
)


class LLMClient(Protocol):
    def chat(self, system: str, user: str, temperature: float = 0.2, **kwargs) -> str: ...
    def chat_stream(self, system: str, user: str, temperature: float = 0.2): ...


@dataclass(frozen=True)
class ReportDocument:
    path: str
    # (heading, text) in reading order -- the same sections the search runs over
    sections: list[tuple[str, str]]

    @property
    def name(self) -> str:
        return Path(self.path.replace("\\", "/")).name

    @property
    def chars(self) -> int:
        return sum(len(h) + len(t) for h, t in self.sections)


@dataclass(frozen=True)
class ReportLimits:
    batch_chars: int  # how much of a document one note-taking call reads
    max_files: int
    max_chars: int  # all documents together
    notes_chars: int  # all notes together, before the report is written


# ---------- pure helpers ----------

def format_section(heading: str, text: str) -> str:
    return f"## {heading}\n{text}" if heading else text


def batch_sections(sections: list[tuple[str, str]], max_chars: int) -> list[str]:
    """Groups consecutive sections into pieces of at most max_chars (a single
    section longer than that is cut at the last space before the limit)."""
    batches: list[str] = []
    current = ""
    for heading, text in sections:
        block = format_section(heading, text)
        while len(block) > max_chars:
            cut = block.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            if current:
                batches.append(current)
                current = ""
            batches.append(block[:cut])
            block = block[cut:].lstrip()
        if not block:
            continue
        if current and len(current) + len(block) + 2 > max_chars:
            batches.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        batches.append(current)
    return batches


_NONE_REPLY = re.compile(r"^\W*none\W*$", re.IGNORECASE)


def is_nothing(notes: str) -> bool:
    """The note-taking reply that means "nothing relevant here"."""
    return not notes.strip() or bool(_NONE_REPLY.match(notes.strip()))


def scope_problem(documents: list[ReportDocument], limits: ReportLimits) -> str | None:
    """A message to show instead of a report when the selection is empty or too big."""
    if not documents:
        return NO_DOCUMENTS_MESSAGE
    total = sum(d.chars for d in documents)
    if len(documents) > limits.max_files:
        return TOO_MANY_MESSAGE.format(detail=f"{len(documents)} documents, the limit is {limits.max_files}")
    if total > limits.max_chars:
        return TOO_MANY_MESSAGE.format(detail=f"about {total // 1000}k characters, the limit is {limits.max_chars // 1000}k")
    return None


def notes_prompt(request: str, notes: dict[str, str], empty: list[str]) -> str:
    parts = [f"Request: {request}", "", "Notes from each document:"]
    for i, (name, text) in enumerate(notes.items(), start=1):
        parts.append(f"\n[{i}] {name}\n{text}")
    if empty:
        parts.append("\nDocuments read that had nothing relevant: " + ", ".join(empty))
    return "\n".join(parts)


# ---------- the report ----------

def _status(text: str) -> StreamEvent:
    return {"event": "status", "data": {"text": text}}


def _nothing_to_report(message: str) -> Iterator[StreamEvent]:
    yield {"event": "meta", "data": {"sources": [], "grounded": False}}
    yield {"event": "token", "data": {"text": message}}
    yield {"event": "extra", "data": {"cross_doc": None, "statistical": None}}


def report_events(
    request: str,
    documents: list[ReportDocument],
    llm: LLMClient,
    limits: ReportLimits,
) -> Iterator[StreamEvent]:
    problem = scope_problem(documents, limits)
    if problem:
        yield from _nothing_to_report(problem)
        return

    notes: dict[str, str] = {}  # path -> notes, for documents with something relevant
    names: dict[str, str] = {}
    empty: list[str] = []

    for number, doc in enumerate(documents, start=1):
        batches = batch_sections(doc.sections, limits.batch_chars)
        collected: list[str] = []
        for part, batch in enumerate(batches, start=1):
            where = f" (part {part} of {len(batches)})" if len(batches) > 1 else ""
            yield _status(f"Reading document {number} of {len(documents)}: {doc.name}{where}…")
            reply = llm.chat(
                MAP_SYSTEM_PROMPT,
                f"Request: {request}\n\nDocument: {doc.name}\n\n{batch}",
                temperature=0.1,
                max_tokens=NOTES_MAX_TOKENS,
                extra_body=_NO_THINKING,
            )
            if not is_nothing(reply):
                collected.append(reply.strip())
        if collected:
            notes[doc.path] = "\n".join(collected)
            names[doc.path] = doc.name
        else:
            empty.append(doc.name)

    if not notes:
        yield from _nothing_to_report(NOTHING_RELEVANT_MESSAGE.format(count=len(documents)))
        return

    # Too much for one prompt: shorten the longest notes, keeping every fact.
    per_document = max(limits.notes_chars // len(notes), 1)
    if sum(len(t) for t in notes.values()) > limits.notes_chars:
        for path, text in list(notes.items()):
            if len(text) > per_document:
                yield _status(f"Condensing notes from {names[path]}…")
                notes[path] = llm.chat(
                    CONDENSE_SYSTEM_PROMPT,
                    f"Keep it under about {per_document} characters.\n\n{text}",
                    temperature=0.1,
                    max_tokens=NOTES_MAX_TOKENS,
                    extra_body=_NO_THINKING,
                ).strip() or text

    yield _status("Writing the report…")
    yield {"event": "meta", "data": {"sources": list(notes), "grounded": True}}
    prompt = notes_prompt(request, {names[p]: t for p, t in notes.items()}, empty)
    yield from _stream_answer(llm, REPORT_SYSTEM_PROMPT, prompt)
    yield {"event": "extra", "data": {"cross_doc": None, "statistical": None}}


# ---------- reading the documents from the database ----------

def load_report_documents(
    session: Session,
    raw_dir: str,
    folders: list[str] | None,
    author: str | None,
    title: str | None,
    file_ids: list[int] | None,
) -> list[ReportDocument]:
    """Every indexed file in the scope, with all its sections in reading order.
    Files that have no sections yet (still being processed) are left out."""
    conditions = file_filter_conditions(raw_dir, folders, author, title, file_ids)
    files = session.execute(select(FileRecord.id, FileRecord.path).where(*conditions).order_by(FileRecord.path)).all()
    if not files:
        return []

    by_file: dict[int, list[tuple[str, str]]] = {file_id: [] for file_id, _ in files}
    rows = session.execute(
        select(ChunkRecord.file_id, ChunkRecord.heading, ChunkRecord.text)
        .where(ChunkRecord.file_id.in_(list(by_file)))
        .order_by(ChunkRecord.file_id, ChunkRecord.chunk_index)
    ).all()
    for file_id, heading, text in rows:
        by_file[file_id].append((heading or "", text))

    return [ReportDocument(path, by_file[file_id]) for file_id, path in files if by_file[file_id]]

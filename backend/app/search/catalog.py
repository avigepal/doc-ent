"""Library questions -- "list all files", "how many documents", "which
files do I have" -- are about the corpus itself, not about what's written
inside it, so embedding search over chunk text can't answer them: the model
only sees 8 excerpts and invents a list from file names it happens to find
in the text. These are answered straight from the `files` table instead.

Split like the rest of search/: detection and formatting are pure and
unit-tested here; the DB read (needs a live Postgres) is `list_catalog`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FileRecord
from app.search.pgvector_retrieval import file_filter_conditions

_NOUN = r"(?:files?|documents?|docs?|pdfs?|attachments?|uploads?)"
_LIST_VERB = r"(?:list|show|display|enumerate|give|write|tell|print|name)"

_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    # "write me all files name", "list all the documents", "show every file"
    rf"\b{_LIST_VERB}\b[^?.!]{{0,40}}\b(?:all|every|each)\b[^?.!]{{0,30}}\b{_NOUN}\b",
    # "give me the names of the files"
    rf"\b{_LIST_VERB}\b[^?.!]{{0,25}}\b(?:names?|titles?)\s+of\b[^?.!]{{0,25}}\b{_NOUN}\b",
    # "file names", "document titles"
    r"\b(?:files?|documents?|docs?)\s*(?:names?|titles?)\b",
    # "how many files"
    rf"\bhow\s+many\b[^?.!]{{0,25}}\b{_NOUN}\b",
    # "which documents are uploaded", "what files do I have"
    rf"\b(?:which|what)\b\s+(?:\w+\s+){{0,2}}{_NOUN}\s+(?:do|are|have|did|is|exist|were|can)\b",
    # bare "list files" / "show documents"
    rf"^\W*(?:please\s+)?{_LIST_VERB}\s+(?:me\s+)?(?:my\s+|the\s+|all\s+)?{_NOUN}\W*$",
)]

# Words after the file noun that mean the user wants files *filtered by
# their content* ("all files that mention X") -- that's a normal search.
_CONTENT_CUE = re.compile(
    r"\b(?:mention\w*|contain\w*|about|regarding|related|relating|discuss\w*|talk\w*|says?|that|where|which)\b",
    re.IGNORECASE,
)

_READY_STATUSES = ("converted", "summarized")
_MAX_LISTED = 100


def is_catalog_question(question: str) -> bool:
    for pattern in _PATTERNS:
        match = pattern.search(question)
        if match and not _CONTENT_CUE.search(question[match.end():]):
            return True
    return False


@dataclass(frozen=True)
class CatalogEntry:
    path: str
    status: str
    title: str | None
    author: str | None
    page_count: int | None


def _display_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _status_label(status: str) -> str:
    if status in _READY_STATUSES:
        return "searchable"
    return {"unsupported": "unsupported file type", "failed": "failed to process"}.get(status, "still processing")


def format_catalog_answer(
    entries: list[CatalogEntry],
    scope: list[str] | None,
    author: str | None = None,
    title: str | None = None,
    attached: bool = False,
) -> tuple[str, list[str]]:
    """Deterministic answer + the file paths to cite. No LLM: the list comes
    from the database, so it can't omit or invent a file."""
    where = "your attached files" if attached else (
        f'folder "{", ".join(scope)}"' if scope and len(scope) == 1 else (
            f'folders {", ".join(scope)}' if scope else "all folders"
        )
    )
    filters = [f'author matching "{author}"' for _ in [0] if author] + [f'title matching "{title}"' for _ in [0] if title]
    where_text = where + (f" ({', '.join(filters)})" if filters else "")

    if not entries:
        return f"No documents found in {where_text}.", []

    ordered = sorted(entries, key=lambda e: _display_name(e.path).lower())
    ready = sum(1 for e in ordered if e.status in _READY_STATUSES)

    noun = "document" if len(ordered) == 1 else "documents"
    header = f"There {'is' if len(ordered) == 1 else 'are'} {len(ordered)} {noun} in {where_text}"
    header += f" ({ready} searchable now)." if ready != len(ordered) else "."

    lines = []
    for i, e in enumerate(ordered[:_MAX_LISTED], start=1):
        details = []
        if e.title and e.title != _display_name(e.path).rsplit(".", 1)[0]:
            details.append(f'title "{e.title}"')
        if e.author:
            details.append(f"by {e.author}")
        if e.page_count:
            details.append(f"{e.page_count} page{'s' if e.page_count != 1 else ''}")
        if e.status not in _READY_STATUSES:
            details.append(_status_label(e.status))
        suffix = f" — {', '.join(details)}" if details else ""
        lines.append(f"{i}. {_display_name(e.path)}{suffix}")

    if len(ordered) > _MAX_LISTED:
        lines.append(f"…and {len(ordered) - _MAX_LISTED} more.")

    return header + "\n\n" + "\n".join(lines), [e.path for e in ordered[:_MAX_LISTED]]


def list_catalog(
    session: Session,
    raw_dir: str,
    folders: list[str] | None = None,
    author: str | None = None,
    title: str | None = None,
    file_ids: list[int] | None = None,
) -> list[CatalogEntry]:
    conditions = file_filter_conditions(raw_dir, folders, author, title, file_ids)
    query = select(
        FileRecord.path, FileRecord.status, FileRecord.title, FileRecord.author, FileRecord.page_count
    )
    if conditions:
        query = query.where(*conditions)
    return [CatalogEntry(*row) for row in session.execute(query).all()]


def catalog_events(answer: str, sources: list[str]) -> Iterator[dict]:
    """Same event shape as search/query.py:stream_query, so the endpoint's
    streaming loop and the dashboard handle a library answer like any other."""
    yield {"event": "meta", "data": {"sources": sources, "grounded": True}}
    yield {"event": "token", "data": {"text": answer}}
    yield {"event": "extra", "data": {"cross_doc": None, "statistical": None}}

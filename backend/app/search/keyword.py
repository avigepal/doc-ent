"""Keyword lookups: a message that is just a word or two ("mac", "ZX-90417",
"wake on lan" in quotes) is a request to find where something is mentioned,
not a question to answer. Answering it with a model summarises whichever
chunks happen to rank first; what the user wants is the list of places that
actually contain the term, with the term highlighted.

Deterministic, no model call, so it's instant. Detection and formatting are
pure and unit-tested here; the SQL (needs a live Postgres) is `find_matches`.
A message that finds nothing falls through to the normal search/chat routing
(a greeting like "hello" is a one-word message too).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import quote

from sqlalchemy import and_, func, literal_column, select
from sqlalchemy.orm import Session

from app.models import ChunkRecord, FileRecord
from app.routing.router import looks_like_edit, looks_like_question
from app.search.hybrid import _STOPWORDS
from app.search.pgvector_retrieval import file_filter_conditions

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_QUOTED = re.compile(r"^\s*[\"“'‘`](.+?)[\"”'’`]\s*$")

MAX_UNQUOTED_WORDS = 2
MAX_QUOTED_WORDS = 6
_MAX_MESSAGE_CHARS = 60

# One-word messages that are conversation, not something to look up.
_NOT_KEYWORDS = frozenset(
    "hi hello hey hola yo thanks thank thx ok okay yes no yep nope sure more again next continue why please bye "
    "goodbye cool great nice good stop help sorry".split()
)

FILES_SHOWN = 20
MATCHES_PER_FILE = 3
_POOL = 300
_SNIPPET_RADIUS = 110


@dataclass(frozen=True)
class KeywordQuery:
    words: tuple[str, ...]
    phrase: bool  # the words must appear next to each other, in order
    display: str


@dataclass(frozen=True)
class Match:
    heading: str
    text: str
    chunk_index: int = 0


@dataclass(frozen=True)
class FileMatches:
    path: str
    sections: int  # chunks of this file that contain the term
    matches: list[Match]
    file_id: int | None = None


@dataclass(frozen=True)
class KeywordResult:
    files: list[FileMatches]
    name_only: list[str]  # files whose name/title matches but whose text doesn't


def parse_keyword_query(message: str) -> KeywordQuery | None:
    """The terms to look up, or None when the message is a question,
    instruction or conversation instead."""
    text = message.strip()
    if not text or len(text) > _MAX_MESSAGE_CHARS or text.endswith("?"):
        return None

    quoted = _QUOTED.match(text)
    inner = quoted.group(1) if quoted else text
    words = _WORD_RE.findall(inner.lower())
    if not words or any(len(w) < 2 for w in words):
        return None

    if quoted:
        if len(words) > MAX_QUOTED_WORDS:
            return None
        return KeywordQuery(tuple(words), phrase=len(words) > 1, display=inner.strip())

    if len(words) > MAX_UNQUOTED_WORDS:
        return None
    if any(w in _STOPWORDS or w in _NOT_KEYWORDS for w in words):
        return None
    if looks_like_edit(text) or looks_like_question(text):
        return None
    # "ZX-90417" is one token to the user: look for it as written
    phrase = len(inner.split()) == 1 and len(words) > 1
    return KeywordQuery(tuple(words), phrase=phrase, display=inner.strip())


def _tsquery_text(query: KeywordQuery) -> str:
    return (" <-> " if query.phrase else " & ").join(query.words)


# ---------- snippets ----------

def _word_pattern(word: str) -> str:
    # a prefix covers the endings the search stems away (compare / comparing)
    stem = word if len(word) < 5 else word[:-1]
    return rf"(?<!\w){re.escape(stem)}\w*"


def _highlight_regex(query: KeywordQuery) -> re.Pattern:
    if query.phrase:
        # the whole phrase as one hit ("Wake-on-LAN"), not each small word in it
        return re.compile(r"\W+".join(_word_pattern(w) for w in query.words), re.IGNORECASE)
    return re.compile("|".join(_word_pattern(w) for w in query.words), re.IGNORECASE)


# table borders, and the bullet-glyph placeholders older conversions left behind
_NOISE = re.compile(r"\s\|\s|^\||\|$|GLYPH(?:<|&lt;)[^>;]*(?:>|&gt;)")
_MD_SPECIAL =re.compile(r"([\\`*_{}\[\]<>#|~])")


def _escape(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


def make_snippet(text: str, query: KeywordQuery, radius: int = _SNIPPET_RADIUS) -> str:
    """The stretch of text around the first hit, with each hit in bold and
    everything else escaped so it can't break the Markdown around it."""
    flat = " ".join(_NOISE.sub(" ", text).split())
    pattern = _highlight_regex(query)
    first = pattern.search(flat)
    if first:
        start = max(0, first.start() - radius)
        end = min(len(flat), first.end() + radius)
        # cut at word boundaries, never through the hit
        if start > 0:
            space = flat.find(" ", start, first.start())
            start = space + 1 if space != -1 else start
        if end < len(flat):
            space = flat.rfind(" ", first.end(), end)
            end = space if space != -1 else end
    else:
        start, end = 0, min(len(flat), radius * 2)
    window = flat[start:end]

    out: list[str] = []
    cursor = 0
    for hit in pattern.finditer(window):
        out.append(_escape(window[cursor:hit.start()]))
        out.append(f"**{_escape(hit.group(0))}**")
        cursor = hit.end()
    out.append(_escape(window[cursor:]))
    return ("…" if start > 0 else "") + "".join(out).strip() + ("…" if end < len(flat) else "")


# ---------- formatting ----------

def _display_name(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def view_link(query: KeywordQuery, *, file_id: int | None = None, path: str | None = None, chunk: int | None = None) -> str:
    """A link the dashboard turns into "open this part of the file"."""
    parts = []
    if file_id is not None:
        parts.append(f"file={file_id}")
    elif path:
        parts.append(f"path={quote(path, safe='')}")
    if chunk is not None:
        parts.append(f"chunk={chunk}")
    parts.append(f"q={quote(query.display, safe='')}")
    if query.phrase:
        parts.append("phrase=1")
    return "#view?" + "&".join(parts)


def format_keyword_answer(query: KeywordQuery, result: KeywordResult) -> tuple[str, list[str]]:
    """Deterministic answer + the file paths to cite."""
    label = f"“{_escape(query.display)}”"
    sections = sum(f.sections for f in result.files)
    lines: list[str] = []

    if result.files:
        files_word = "file" if len(result.files) == 1 else "files"
        sections_word = "section" if sections == 1 else "sections"
        lines.append(f"**{sections} {sections_word} mention {label} in {len(result.files)} {files_word}**")
        lines.append("")
        for i, entry in enumerate(result.files, start=1):
            count = f"{entry.sections} section{'s' if entry.sections != 1 else ''}"
            lines.append(f"**{i}. {_escape(_display_name(entry.path))}** — {count}")
            for match in entry.matches:
                where = f" — *{_escape(match.heading)}*" if match.heading else ""
                link = view_link(query, file_id=entry.file_id, chunk=match.chunk_index) if entry.file_id is not None else None
                opener = f" · [open]({link})" if link else ""
                lines.append(f"- {make_snippet(match.text, query)}{where}{opener}")
            lines.append("")
        if any(f.sections > len(f.matches) for f in result.files):
            lines.append(f"Showing the best {MATCHES_PER_FILE} sections per file.")

    if result.name_only:
        if lines:
            lines.append("")
        lines.append(f"**Files with {label} in the name**")
        lines.append("")
        lines.extend(
            f"- {_escape(_display_name(p))} · [open]({view_link(query, path=p)})" for p in result.name_only
        )

    lines.append("")
    lines.append("Ask a question about any of these to get an answer.")

    sources = [f.path for f in result.files] + result.name_only
    return "\n".join(lines).strip(), sources


# ---------- database ----------

def find_matches(
    session: Session,
    query: KeywordQuery,
    raw_dir: str | None = None,
    folders: list[str] | None = None,
    author: str | None = None,
    title: str | None = None,
    file_ids: list[int] | None = None,
) -> KeywordResult:
    """Where the term appears. Text matches come from the chunks' full-text
    index (stemmed: "comparing" finds "compare"), best-ranked first, grouped
    by file. Files whose name/title/author match but whose text doesn't are
    listed separately -- a file called "Mac Studio.pdf" matches "mac" even if
    the word never appears inside it."""
    conditions = file_filter_conditions(raw_dir, folders, author, title, file_ids)
    tsquery = _tsquery_text(query)
    chunk_q = func.to_tsquery(literal_column("'english'::regconfig"), tsquery)
    in_text = ChunkRecord.search_vector.op("@@")(chunk_q)

    per_file = session.execute(
        select(FileRecord.id, FileRecord.path, func.count(ChunkRecord.id).label("sections"))
        .join(ChunkRecord, ChunkRecord.file_id == FileRecord.id)
        .where(and_(in_text, *conditions))
        .group_by(FileRecord.id, FileRecord.path)
        .order_by(func.count(ChunkRecord.id).desc(), FileRecord.path)
        .limit(FILES_SHOWN)
    ).all()

    files: list[FileMatches] = []
    if per_file:
        rank = func.ts_rank_cd(ChunkRecord.search_vector, chunk_q)
        rows = session.execute(
            select(ChunkRecord.file_id, ChunkRecord.heading, ChunkRecord.text, ChunkRecord.chunk_index)
            .where(and_(in_text, ChunkRecord.file_id.in_([row.id for row in per_file])))
            .order_by(rank.desc(), ChunkRecord.chunk_index)
            .limit(_POOL)
        ).all()
        grouped: dict[int, list[Match]] = {row.id: [] for row in per_file}
        for file_id, heading, text, chunk_index in rows:
            bucket = grouped.get(file_id)
            if bucket is not None and len(bucket) < MATCHES_PER_FILE:
                bucket.append(Match(heading or "", text, chunk_index))
        files = [FileMatches(row.path, row.sections, grouped[row.id], row.id) for row in per_file]

    meta_q = func.to_tsquery(literal_column("'simple'::regconfig"), " & ".join(query.words))
    listed = {f.path for f in files}
    name_rows = session.execute(
        select(FileRecord.path)
        .where(and_(FileRecord.meta_vector.op("@@")(meta_q), *conditions))
        .order_by(FileRecord.path)
        .limit(FILES_SHOWN)
    ).scalars().all()
    name_only = [p for p in name_rows if p not in listed]

    return KeywordResult(files, name_only)


def keyword_events(
    session: Session,
    query: KeywordQuery,
    raw_dir: str | None,
    folders: list[str] | None,
    author: str | None,
    title: str | None,
    file_ids: list[int] | None,
) -> Iterator[dict] | None:
    """Events in the same shape as any other answer, or None when nothing
    matched (the caller then routes the message normally)."""
    from app.search.catalog import catalog_events

    result = find_matches(session, query, raw_dir, folders, author, title, file_ids)
    if not result.files and not result.name_only:
        return None
    answer, sources = format_keyword_answer(query, result)
    return catalog_events(answer, sources)

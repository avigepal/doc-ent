"""The document viewer's data: a window of one file's sections around a spot.

Search results know exactly which section matched (`around`); an answer's
sources only know the file, so the section that best matches the question
is found here (`q`). Sections are the same chunks the search runs over, in
reading order, so what you see is what was searched.
"""

from __future__ import annotations

import re

from sqlalchemy import and_, func, literal_column, select
from sqlalchemy.orm import Session

from app.models import ChunkRecord, FileRecord
from app.search.hybrid import build_tsquery_terms

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def highlight_terms(q: str | None, phrase: bool) -> list[str]:
    """The words to highlight: a phrase keeps every word, a question only its
    content words."""
    if not q:
        return []
    if phrase:
        return [w for w in _WORD_RE.findall(q.lower()) if len(w) >= 2][:6]
    return build_tsquery_terms(q)


def best_section(session: Session, file_id: int, terms: list[str], phrase: bool) -> int | None:
    """Index of the section of this file that matches the terms best."""
    if not terms:
        return None
    tsquery = func.to_tsquery(literal_column("'english'::regconfig"), (" <-> " if phrase else " | ").join(terms))
    return session.execute(
        select(ChunkRecord.chunk_index)
        .where(and_(ChunkRecord.file_id == file_id, ChunkRecord.search_vector.op("@@")(tsquery)))
        .order_by(func.ts_rank_cd(ChunkRecord.search_vector, tsquery).desc(), ChunkRecord.chunk_index)
        .limit(1)
    ).scalar_one_or_none()


def load_sections(
    session: Session,
    *,
    file_id: int | None,
    path: str | None,
    around: int | None,
    span: int,
    q: str | None,
    phrase: bool,
) -> dict | None:
    if file_id is not None:
        record = session.get(FileRecord, file_id)
    elif path:
        record = session.execute(select(FileRecord).where(FileRecord.path == path)).scalars().first()
    else:
        record = None
    if record is None:
        return None

    first, last, total = session.execute(
        select(func.min(ChunkRecord.chunk_index), func.max(ChunkRecord.chunk_index), func.count())
        .where(ChunkRecord.file_id == record.id)
    ).one()
    if not total:
        return None

    terms = highlight_terms(q, phrase)
    anchor = around
    if anchor is None:
        anchor = best_section(session, record.id, terms, phrase)
    if anchor is None:
        anchor = first
    anchor = min(max(anchor, first), last)

    start = max(first, anchor - span)
    end = min(last, anchor + span)
    rows = session.execute(
        select(ChunkRecord.chunk_index, ChunkRecord.heading, ChunkRecord.text)
        .where(and_(ChunkRecord.file_id == record.id, ChunkRecord.chunk_index.between(start, end)))
        .order_by(ChunkRecord.chunk_index)
    ).all()

    return {
        "file": {"id": record.id, "name": record.path.replace("\\", "/").rsplit("/", 1)[-1], "path": record.path},
        "total": total,
        "anchor": anchor,
        "has_before": start > first,
        "has_after": end < last,
        "terms": terms,
        "phrase": phrase,
        "sections": [{"index": i, "heading": h or "", "text": t} for i, h, t in rows],
    }

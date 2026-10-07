"""Phase 4: pgvector similarity retrieval. Needs a live Postgres+pgvector
database with populated embeddings — not unit-testable without one, so
this stays a thin wrapper around a SQL query (same pattern as the DB
code in app/main.py). Everything downstream of RetrievedChunk (ask.py,
correlate.py) is tested with fakes instead; the folder-filter pattern
building is factored into folder_filter.py and tested there.
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import ChunkRecord, FileRecord
from app.search.folder_filter import build_folder_like_patterns
from app.search.retrieval import RetrievedChunk


def retrieve_top_k(
    session: Session,
    query_embedding: list[float],
    k: int = 8,
    raw_dir: str | None = None,
    folders: list[str] | None = None,
    author: str | None = None,
    title: str | None = None,
) -> list[RetrievedChunk]:
    """Cosine-distance nearest neighbors via pgvector's <=> operator.
    1 - distance is used as the similarity score so higher is better,
    matching RetrievedChunk.score's convention.

    `folders` scopes to files under those top-level raw/ folders (both
    raw_dir and folders must be given together). `author`/`title` filter
    on the document-intrinsic metadata extracted at convert time (see
    app/tasks/convert.py:_apply_document_metadata) — case-insensitive
    substring match, e.g. author="alice" matches "alice@example.com".
    Filters combine with AND; omitting all of them searches everything.
    """
    distance = ChunkRecord.embedding.cosine_distance(query_embedding)
    query = (
        select(ChunkRecord.text, FileRecord.path, distance.label("distance"))
        .join(FileRecord, FileRecord.id == ChunkRecord.file_id)
    )

    conditions = []

    if raw_dir and folders:
        patterns = build_folder_like_patterns(raw_dir, folders)
        if patterns:
            conditions.append(or_(*(FileRecord.path.like(p) for p in patterns)))

    if author:
        conditions.append(FileRecord.author.ilike(f"%{author}%"))

    if title:
        conditions.append(FileRecord.title.ilike(f"%{title}%"))

    if conditions:
        query = query.where(and_(*conditions))

    rows = session.execute(query.order_by(distance).limit(k)).all()

    return [
        RetrievedChunk(file_path=path, heading="", text=text, score=1 - dist)
        for text, path, dist in rows
    ]

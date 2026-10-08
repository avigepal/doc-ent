"""Phase 4: hybrid retrieval over Postgres. Needs a live Postgres+pgvector
database with populated embeddings — not unit-testable without one, so
this stays a thin wrapper around SQL (same pattern as the DB code in
app/main.py). The pure parts (query building, rank fusion) live in
hybrid.py and are tested there; everything downstream of RetrievedChunk
(ask.py, correlate.py) is tested with fakes; the folder-filter pattern
building is factored into folder_filter.py and tested there.

Two searches run and are merged with Reciprocal Rank Fusion:
  * vector: cosine nearest neighbours on chunks.embedding (meaning)
  * keyword: full-text match on chunk text/heading AND on the file's
    title/author/filename (exact terms, names, IDs, "files by alice")
"""

from __future__ import annotations

from sqlalchemy import and_, func, literal_column, or_, select
from sqlalchemy.orm import Session

from app.models import ChunkRecord, FileRecord
from app.search.folder_filter import build_folder_like_patterns
from app.search.hybrid import Candidate, build_or_tsquery, fuse
from app.search.retrieval import RetrievedChunk

# Each side fetches more than k so fusion has overlap to work with.
_CANDIDATE_MULTIPLIER = 3
_MIN_CANDIDATES = 20


def file_filter_conditions(
    raw_dir: str | None,
    folders: list[str] | None,
    author: str | None,
    title: str | None,
    file_ids: list[int] | None = None,
) -> list:
    conditions = []

    if file_ids:
        # a chat's attached files: search exactly these, whatever folders are
        # selected in Scope
        conditions.append(FileRecord.id.in_(file_ids))
    elif raw_dir and folders:
        patterns = build_folder_like_patterns(raw_dir, folders)
        if patterns:
            conditions.append(or_(*(FileRecord.path.like(p) for p in patterns)))

    if author:
        conditions.append(FileRecord.author.ilike(f"%{author}%"))

    if title:
        conditions.append(FileRecord.title.ilike(f"%{title}%"))

    return conditions


def _to_candidates(rows) -> list[Candidate]:
    return [
        Candidate(
            chunk_id=chunk_id,
            file_path=path,
            heading=heading or "",
            text=text,
            similarity=None if dist is None else 1 - dist,
        )
        for chunk_id, text, heading, path, dist in rows
    ]


def retrieve_top_k(
    session: Session,
    query_embedding: list[float],
    k: int = 8,
    raw_dir: str | None = None,
    folders: list[str] | None = None,
    author: str | None = None,
    title: str | None = None,
    query_text: str | None = None,
    file_ids: list[int] | None = None,
) -> list[RetrievedChunk]:
    """Hybrid search. `query_text` is the user's question; when given, a
    keyword/metadata search runs alongside the vector search. Without it
    this is vector-only, as before.

    Score on the returned chunks is 1 - cosine_distance (higher is
    better, matching RetrievedChunk.score), raised to a floor for chunks
    that matched on keywords/metadata so exact-term hits aren't rejected
    by the caller's similarity threshold.

    `folders` scopes to files under those top-level raw/ folders (both
    raw_dir and folders must be given together). `author`/`title` filter
    on the document-intrinsic metadata extracted at convert time (see
    app/tasks/convert.py:_apply_document_metadata) — case-insensitive
    substring match, e.g. author="alice" matches "alice@example.com".
    Filters combine with AND; omitting all of them searches everything.
    """
    conditions = file_filter_conditions(raw_dir, folders, author, title, file_ids)
    pool = max(k * _CANDIDATE_MULTIPLIER, _MIN_CANDIDATES)
    distance = ChunkRecord.embedding.cosine_distance(query_embedding)
    columns = (ChunkRecord.id, ChunkRecord.text, ChunkRecord.heading, FileRecord.path, distance.label("distance"))

    # Rows without an embedding (not indexed yet, or the embed call failed)
    # can't be ranked by distance; excluding them also keeps NULL
    # distances out of the score arithmetic.
    vector_query = (
        select(*columns)
        .join(FileRecord, FileRecord.id == ChunkRecord.file_id)
        .where(and_(ChunkRecord.embedding.is_not(None), *conditions))
        .order_by(distance)
        .limit(pool)
    )
    vector_hits = _to_candidates(session.execute(vector_query).all())

    keyword_hits: list[Candidate] = []
    tsquery_text = build_or_tsquery(query_text) if query_text else None
    if tsquery_text:
        # explicit regconfig cast: a bound string param can arrive typed as text,
        # and to_tsquery(text, text) does not exist.
        chunk_q = func.to_tsquery(literal_column("'english'::regconfig"), tsquery_text)
        meta_q = func.to_tsquery(literal_column("'simple'::regconfig"), tsquery_text)
        # Metadata matches count double: a hit on a file's title/author/name
        # says more about what the user wants than a word in a paragraph.
        rank = func.ts_rank_cd(ChunkRecord.search_vector, chunk_q) + 2 * func.ts_rank(FileRecord.meta_vector, meta_q)
        keyword_query = (
            select(*columns)
            .join(FileRecord, FileRecord.id == ChunkRecord.file_id)
            .where(
                and_(
                    or_(ChunkRecord.search_vector.op("@@")(chunk_q), FileRecord.meta_vector.op("@@")(meta_q)),
                    *conditions,
                )
            )
            # chunk_index tie-break: a metadata-only match is the same for
            # every chunk of the file, so prefer its opening sections.
            .order_by(rank.desc(), ChunkRecord.chunk_index)
            .limit(pool)
        )
        keyword_hits = _to_candidates(session.execute(keyword_query).all())

    return [
        RetrievedChunk(file_path=c.file_path, heading=c.heading, text=c.text, score=score)
        for c, score in fuse(vector_hits, keyword_hits, k)
    ]

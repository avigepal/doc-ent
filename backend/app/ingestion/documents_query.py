"""Corpus-wide file listing for the Documents page. One call across all
folders — the Documents table would otherwise need N round trips to the
per-folder /folders/{name}/files endpoint.

Folder scoping reuses build_folder_like_patterns rather than building its
own LIKE pattern: that helper already handles PostgreSQL treating a
backslash as its escape character, which silently broke folder filtering
on Windows paths before.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import FileRecord
from app.search.folder_filter import build_folder_like_patterns


def relative_to_raw(path: str, raw_dir: str) -> str:
    """Trims the raw/ prefix so the UI shows "contracts/lease.pdf" rather
    than the full container path."""
    base = raw_dir.rstrip("/\\")
    for separator in ("/", "\\"):
        prefix = base + separator
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def list_documents(
    session: Session,
    *,
    raw_dir: str,
    folder: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict], int]:
    conditions = []

    if folder:
        patterns = build_folder_like_patterns(raw_dir, [folder])
        if patterns:
            conditions.append(or_(*(FileRecord.path.like(p) for p in patterns)))

    if status:
        conditions.append(FileRecord.status == status)

    if search:
        like = f"%{search}%"
        conditions.append(
            or_(
                FileRecord.path.ilike(like),
                FileRecord.title.ilike(like),
                FileRecord.author.ilike(like),
            )
        )

    rows_stmt = select(FileRecord)
    count_stmt = select(func.count()).select_from(FileRecord)
    if conditions:
        rows_stmt = rows_stmt.where(*conditions)
        count_stmt = count_stmt.where(*conditions)

    total = session.execute(count_stmt).scalar_one()
    rows = session.execute(
        rows_stmt.order_by(FileRecord.discovered_at.desc()).limit(limit).offset(offset)
    ).scalars()

    documents = [
        {
            "id": row.id,
            "path": relative_to_raw(row.path, raw_dir),
            "title": row.title,
            "author": row.author,
            "mime_type": row.mime_type,
            "size_bytes": row.size_bytes,
            "page_count": row.page_count,
            "status": row.status,
            "queue": row.queue,
            "discovered_at": row.discovered_at.isoformat() if row.discovered_at else None,
            "doc_created_at": row.doc_created_at.isoformat() if row.doc_created_at else None,
        }
        for row in rows
    ]
    return documents, total

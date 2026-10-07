"""DB access for query/export history. Thin wrappers around SQLAlchemy —
the shaping logic they feed lives in serialization.py and is tested
there; this layer is covered by the import smoke test and the end-to-end
pass, matching how the rest of the DB code in this project is handled.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExportHistoryRecord, QueryHistoryRecord


def record_query(
    session: Session,
    *,
    question: str,
    result: dict,
    folders: list[str] | None = None,
    author: str | None = None,
    title: str | None = None,
    attached_filenames: list[str] | None = None,
) -> int:
    """Stores a completed query result and returns the new row's id, which
    the endpoint hands back to the client so a later /export can link to
    the query it came from."""
    record = QueryHistoryRecord(
        question=question,
        answer=result.get("answer", ""),
        sources=result.get("sources") or [],
        grounded=bool(result.get("grounded", False)),
        cross_doc=result.get("cross_doc"),
        statistical=result.get("statistical"),
        filter_folders=folders or [],
        filter_author=author,
        filter_title=title,
        attached_filenames=attached_filenames or [],
    )
    session.add(record)
    session.commit()
    return record.id


def record_export(
    session: Session,
    *,
    query_history_id: int | None,
    filename: str,
    fmt: str,
    stored_path: Path,
) -> int:
    size_bytes = stored_path.stat().st_size if stored_path.exists() else 0
    record = ExportHistoryRecord(
        query_history_id=query_history_id,
        filename=filename,
        fmt=fmt,
        stored_path=str(stored_path),
        size_bytes=size_bytes,
    )
    session.add(record)
    session.commit()
    return record.id


def list_queries(session: Session, limit: int = 50, offset: int = 0) -> list[QueryHistoryRecord]:
    stmt = (
        select(QueryHistoryRecord)
        .order_by(QueryHistoryRecord.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.execute(stmt).scalars())


def get_query(session: Session, history_id: int) -> QueryHistoryRecord | None:
    return session.get(QueryHistoryRecord, history_id)


def delete_query(session: Session, history_id: int) -> bool:
    record = session.get(QueryHistoryRecord, history_id)
    if record is None:
        return False
    session.delete(record)
    session.commit()
    return True


def list_exports(session: Session, limit: int = 50, offset: int = 0) -> list[ExportHistoryRecord]:
    stmt = (
        select(ExportHistoryRecord)
        .order_by(ExportHistoryRecord.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.execute(stmt).scalars())


def get_export(session: Session, export_id: int) -> ExportHistoryRecord | None:
    return session.get(ExportHistoryRecord, export_id)

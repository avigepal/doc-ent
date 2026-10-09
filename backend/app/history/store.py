"""DB access for query/export history. Thin wrappers around SQLAlchemy —
the shaping logic they feed lives in serialization.py and is tested
there; this layer is covered by the import smoke test and the end-to-end
pass, matching how the rest of the DB code in this project is handled.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import ConversationPin, ExportHistoryRecord, QueryHistoryRecord


def record_query(
    session: Session,
    *,
    question: str,
    result: dict,
    folders: list[str] | None = None,
    author: str | None = None,
    title: str | None = None,
    attached_filenames: list[str] | None = None,
    chat_only: bool = False,
    conversation_id: str = "",
    route: str = "",
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
        chat_only=chat_only,
        conversation_id=conversation_id,
        route=route,
    )
    session.add(record)
    session.commit()
    return record.id


def set_cross_doc(session: Session, history_id: int, cross_doc: dict) -> None:
    """Saves cross-document findings the user asked for after the answer, so
    the stored turn shows them when the chat is restored."""
    session.execute(update(QueryHistoryRecord).where(QueryHistoryRecord.id == history_id).values(cross_doc=cross_doc))
    session.commit()


def record_export(
    session: Session,
    *,
    query_history_id: int | None,
    filename: str,
    fmt: str,
    stored_path: Path,
    source_name: str | None = None,
    source_file_id: int | None = None,
) -> int:
    size_bytes = stored_path.stat().st_size if stored_path.exists() else 0
    record = ExportHistoryRecord(
        query_history_id=query_history_id,
        filename=filename,
        fmt=fmt,
        stored_path=str(stored_path),
        size_bytes=size_bytes,
        source_name=source_name,
        source_file_id=source_file_id,
    )
    session.add(record)
    session.commit()
    return record.id


def edits_in_conversation(
    session: Session,
    conversation_id: str,
    source_file_id: int,
    before_history_id: int | None = None,
) -> list[ExportHistoryRecord]:
    """The files earlier edits of this uploaded file made in this chat,
    newest first. `before_history_id` limits it to turns before that one, so
    regenerating an earlier edit continues from what came before it."""
    if not conversation_id:
        return []
    stmt = (
        select(ExportHistoryRecord)
        .join(QueryHistoryRecord, QueryHistoryRecord.id == ExportHistoryRecord.query_history_id)
        .where(
            QueryHistoryRecord.conversation_id == conversation_id,
            ExportHistoryRecord.source_file_id == source_file_id,
        )
        .order_by(ExportHistoryRecord.id.desc())
    )
    if before_history_id is not None:
        stmt = stmt.where(QueryHistoryRecord.id < before_history_id)
    return list(session.execute(stmt).scalars())


def exports_by_query(session: Session, query_ids: list[int]) -> dict[int, list[ExportHistoryRecord]]:
    """The files each of these history rows generated, oldest first."""
    grouped: dict[int, list[ExportHistoryRecord]] = {}
    if not query_ids:
        return grouped
    rows = session.execute(
        select(ExportHistoryRecord)
        .where(ExportHistoryRecord.query_history_id.in_(query_ids))
        .order_by(ExportHistoryRecord.id)
    ).scalars()
    for row in rows:
        grouped.setdefault(row.query_history_id, []).append(row)
    return grouped


def link_exports(session: Session, export_ids: list[int], query_history_id: int) -> None:
    """Attach files generated while answering a query to that query's history
    row (the files are recorded before the row exists)."""
    if not export_ids:
        return
    session.execute(
        update(ExportHistoryRecord)
        .where(ExportHistoryRecord.id.in_(export_ids))
        .values(query_history_id=query_history_id)
    )
    session.commit()


def list_queries(
    session: Session, limit: int = 50, offset: int = 0, conversation_id: str | None = None
) -> list[QueryHistoryRecord]:
    stmt = select(QueryHistoryRecord).order_by(QueryHistoryRecord.created_at.desc())
    if conversation_id is not None:
        stmt = stmt.where(QueryHistoryRecord.conversation_id == conversation_id)
    stmt = stmt.limit(limit).offset(offset)
    return list(session.execute(stmt).scalars())


def list_conversations(session: Session, limit: int = 50) -> list[dict]:
    """One row per conversation_id (the Ask page's "chats" list in the
    sidebar) — title is the opening question (array_agg ordered by
    created_at ASC, first element), not the most recent one, so it reads
    like a chat title rather than changing every time the conversation
    gets a new message. '' (rows from before this column existed) is
    excluded — those are legacy, ungrouped history, not a real chat.
    Pinned chats come first (most recently pinned on top), then the rest
    by latest activity."""
    title = func.array_agg(aggregate_order_by(QueryHistoryRecord.question, QueryHistoryRecord.created_at.asc()))[1]
    stmt = (
        select(
            QueryHistoryRecord.conversation_id,
            title.label("title"),
            func.max(QueryHistoryRecord.created_at).label("last_at"),
            func.count().label("message_count"),
            ConversationPin.pinned_at,
        )
        .select_from(QueryHistoryRecord)
        .outerjoin(ConversationPin, ConversationPin.conversation_id == QueryHistoryRecord.conversation_id)
        .where(QueryHistoryRecord.conversation_id != "")
        .group_by(QueryHistoryRecord.conversation_id, ConversationPin.pinned_at)
        .order_by(ConversationPin.pinned_at.desc().nulls_last(), func.max(QueryHistoryRecord.created_at).desc())
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    return [
        {
            "conversation_id": r.conversation_id,
            "title": r.title,
            "last_at": r.last_at,
            "message_count": r.message_count,
            "pinned": r.pinned_at is not None,
        }
        for r in rows
    ]


def set_conversation_pinned(session: Session, conversation_id: str, pinned: bool) -> None:
    if pinned:
        session.execute(
            pg_insert(ConversationPin).values(conversation_id=conversation_id).on_conflict_do_nothing()
        )
    else:
        session.execute(delete(ConversationPin).where(ConversationPin.conversation_id == conversation_id))
    session.commit()


def delete_conversation(session: Session, conversation_id: str) -> int:
    """Removes every message in the chat (and its pin); returns how many
    messages went. '' is refused — that's the bucket of legacy rows from
    before conversations existed, not a chat, and "delete conversation ''"
    must never wipe all of them."""
    if not conversation_id:
        return 0
    result = session.execute(
        delete(QueryHistoryRecord).where(QueryHistoryRecord.conversation_id == conversation_id)
    )
    session.execute(delete(ConversationPin).where(ConversationPin.conversation_id == conversation_id))
    session.commit()
    return result.rowcount


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

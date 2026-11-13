"""Keeping "who wrote to whom" for every email file, so it can be answered exactly instead of searched for.

The email converter (app/conversion/email_backend.py) returns one entry per message in
engine_metadata["messages"]. `store_email_links` turns those into rows: one per (message, recipient).
A recipient who is in both To and Cc counts once (as To). `backfill_email_links` does the same for
email files that were converted before this existed, by reading the raw files again (no model involved).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import EmailLinkRecord, FileRecord

logger = logging.getLogger(__name__)

EMAIL_QUEUE = "convert_email_archive"


def _addr(person: dict | None) -> str:
    return (person or {}).get("addr", "") or ""


def _name(person: dict | None) -> str:
    return (person or {}).get("name", "") or ""


def _sent_at(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def link_rows(file_id: int, messages: list[dict]) -> list[EmailLinkRecord]:
    rows: list[EmailLinkRecord] = []
    for message in messages:
        sender = message.get("sender")
        base = dict(
            file_id=file_id,
            msg_index=int(message.get("index", 0)),
            subject=message.get("subject", "") or "",
            sent_at=_sent_at(message.get("sent_at")),
            sender_name=_name(sender),
            sender_addr=_addr(sender),
        )
        recipients: dict[str, tuple[str, str]] = {}
        for kind, people in (("to", message.get("to") or []), ("cc", message.get("cc") or [])):
            for person in people:
                addr = _addr(person)
                if addr and addr not in recipients:
                    recipients[addr] = (kind, _name(person))
        if not recipients:
            rows.append(EmailLinkRecord(**base, recipient_addr="", recipient_name="", kind="to"))  # still counts as sent
            continue
        for addr, (kind, name) in recipients.items():
            rows.append(EmailLinkRecord(**base, recipient_addr=addr, recipient_name=name, kind=kind))
    return rows


def store_email_links(session: Session, file_id: int, messages: list[dict] | None) -> int:
    """Replace this file's rows with the ones for `messages`. Returns how many rows were written."""
    if messages is None:
        return 0
    session.execute(delete(EmailLinkRecord).where(EmailLinkRecord.file_id == file_id))
    rows = link_rows(file_id, messages)
    session.add_all(rows)
    return len(rows)


def backfill_email_links(session: Session, only_missing: bool = True) -> dict:
    """Rows for email files converted before who-mailed-whom existed. Reads each raw file again."""
    from app.conversion.email_backend import EmailArchiveBackend

    has_rows = select(EmailLinkRecord.file_id).distinct()
    query = select(FileRecord).where(FileRecord.queue == EMAIL_QUEUE, FileRecord.status.in_(("converted", "summarized")))
    if only_missing:
        query = query.where(FileRecord.id.not_in(has_rows))
    files = session.execute(query).scalars().all()

    backend = EmailArchiveBackend()
    done = failed = rows = 0
    for file_record in files:
        try:
            result = backend.convert(Path(file_record.path))
            rows += store_email_links(session, file_record.id, result.engine_metadata.get("messages"))
            session.commit()
            done += 1
        except Exception:
            session.rollback()
            failed += 1
            logger.warning("could not read emails from %s", file_record.path, exc_info=True)
    return {"files": len(files), "done": done, "failed": failed, "rows": rows}

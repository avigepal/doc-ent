"""Filtering the stored emails by sender, recipient, date and folder: the Emails view on the Documents page.

The rows come from email_links (see app/ingestion/email_store.py): one per (message, recipient). A
message is listed once, with all its recipients. Filters are applied per message:

    sender     words that must all appear in the sender's name or address
    recipient  words that must all appear in one recipient's name or address (To or Cc)
    after      sent on or after this day (UTC)
    before     sent on or before this day (UTC)
    folder     only emails from files under this folder (and its subfolders)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import array_agg
from sqlalchemy.orm import Session

from app.models import EmailLinkRecord as L
from app.models import FileRecord
from app.search.pgvector_retrieval import file_filter_conditions

MAX_PAGE = 200


@dataclass(frozen=True)
class EmailFilter:
    sender: str = ""
    recipient: str = ""
    after: date | None = None
    before: date | None = None
    folder: str = ""

    def __post_init__(self) -> None:
        if self.after and self.before and self.after > self.before:
            raise ValueError("the first date is after the last date")


def parse_day(text: str | None) -> date | None:
    """"2024-03-31" as a date; empty means no limit."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"dates look like 2024-03-31, not {text!r}")


def _words(text: str) -> list[str]:
    return [word for word in text.split() if word]


def _person(name_col, addr_col, text: str):
    """Every word must appear in the name or the address (case-insensitive)."""
    return and_(*(or_(name_col.icontains(w, autoescape=True), addr_col.icontains(w, autoescape=True)) for w in _words(text)))


def _folder_of(path: str, raw_dir: Path) -> str:
    try:
        parent = Path(path).parent.relative_to(raw_dir)
    except ValueError:
        return ""
    return "" if parent == Path(".") else parent.as_posix()


def list_emails(session: Session, flt: EmailFilter, raw_dir: Path, offset: int = 0, limit: int = 50) -> dict:
    conditions = list(file_filter_conditions(str(raw_dir), [flt.folder] if flt.folder else None, None, None))
    if flt.sender:
        conditions.append(_person(L.sender_name, L.sender_addr, flt.sender))
    if flt.after:
        conditions.append(L.sent_at >= datetime.combine(flt.after, time.min, tzinfo=timezone.utc))
    if flt.before:
        conditions.append(L.sent_at < datetime.combine(flt.before + timedelta(days=1), time.min, tzinfo=timezone.utc))

    messages = (
        select(
            L.file_id,
            L.msg_index,
            func.max(L.subject).label("subject"),
            func.min(L.sent_at).label("sent_at"),
            func.max(L.sender_name).label("sender_name"),
            func.max(L.sender_addr).label("sender_addr"),
            array_agg(func.distinct(L.recipient_addr)).label("recipients"),
            FileRecord.path.label("path"),
        )
        .join(FileRecord, FileRecord.id == L.file_id)
        .where(*conditions)
        .group_by(L.file_id, L.msg_index, FileRecord.path)
    )
    if flt.recipient:
        # the message counts when any one of its recipients matches
        messages = messages.having(func.bool_or(_person(L.recipient_name, L.recipient_addr, flt.recipient)))

    total = session.execute(select(func.count()).select_from(messages.subquery())).scalar_one()
    rows = session.execute(
        messages.order_by(func.min(L.sent_at).desc().nulls_last(), L.file_id, L.msg_index)
        .offset(offset)
        .limit(min(limit, MAX_PAGE))
    ).all()

    raw_root = raw_dir.resolve()
    return {
        "total": total,
        "messages": [
            {
                "file_id": file_id,
                "file": Path(path).name,
                "folder": _folder_of(path, raw_root),
                "subject": subject or "",
                "sent_at": sent_at.isoformat() if sent_at else None,
                "sender": {"name": sender_name or "", "addr": sender_addr or ""},
                "recipients": sorted(addr for addr in recipients or [] if addr),
            }
            for file_id, _index, subject, sent_at, sender_name, sender_addr, recipients, path in rows
        ],
    }

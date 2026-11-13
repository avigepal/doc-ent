"""Saved correlation views: the standing dashboard's rows (see app/models.py:SavedViewRecord)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SavedViewRecord

MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 24 * 31


def view_to_dict(row: SavedViewRecord) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "question": row.question,
        "folders": list(row.folders or []),
        "interval_hours": row.interval_hours,
        "last_result": row.last_result,
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
    }


def list_views(session: Session) -> list[dict]:
    rows = session.scalars(select(SavedViewRecord).order_by(SavedViewRecord.created_at.desc())).all()
    return [view_to_dict(r) for r in rows]


def create_view(session: Session, name: str, question: str, folders: list[str], interval_hours: int | None) -> dict:
    if interval_hours is not None and not (MIN_INTERVAL_HOURS <= interval_hours <= MAX_INTERVAL_HOURS):
        raise ValueError(f"interval_hours must be between {MIN_INTERVAL_HOURS} and {MAX_INTERVAL_HOURS}")
    row = SavedViewRecord(name=name.strip()[:120], question=question.strip(), folders=list(folders), interval_hours=interval_hours)
    session.add(row)
    session.commit()
    return view_to_dict(row)


def delete_view(session: Session, view_id: int) -> bool:
    row = session.get(SavedViewRecord, view_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def due_view_ids(session: Session, now: datetime | None = None) -> list[int]:
    """Scheduled views whose interval has passed since their last run (or that have never run)."""
    now = now or datetime.now(timezone.utc)
    due = []
    for row in session.scalars(select(SavedViewRecord).where(SavedViewRecord.interval_hours.is_not(None))).all():
        if row.last_run_at is None or row.last_run_at + timedelta(hours=row.interval_hours) <= now:
            due.append(row.id)
    return due

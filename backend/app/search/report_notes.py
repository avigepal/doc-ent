"""Saved report notes (see ReportNotesRecord): the database side of the cache
the report engine in report.py reads and writes. A failure here must never
break a report -- the notes can always be read again -- so every call falls
back to "not saved".
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import ReportNotesRecord

logger = logging.getLogger(__name__)


class DbNotesStore:
    def __init__(self, session: Session):
        self._session = session

    def get(self, file_id: int, request_key: str, content_hash: str) -> str | None:
        """The saved notes ("" = nothing relevant), or None when there are none
        for this exact text of the file."""
        try:
            return self._session.execute(
                select(ReportNotesRecord.notes).where(
                    ReportNotesRecord.file_id == file_id,
                    ReportNotesRecord.request_key == request_key,
                    ReportNotesRecord.content_hash == content_hash,
                )
            ).scalar_one_or_none()
        except Exception:
            logger.warning("could not read saved report notes", exc_info=True)
            self._session.rollback()
            return None

    def put(self, file_id: int, request_key: str, content_hash: str, notes: str) -> None:
        try:
            stmt = pg_insert(ReportNotesRecord).values(
                file_id=file_id, request_key=request_key, content_hash=content_hash, notes=notes
            )
            self._session.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_report_notes_file_request",
                    set_={"content_hash": content_hash, "notes": notes},
                )
            )
            self._session.commit()
        except Exception:
            logger.warning("could not save report notes", exc_info=True)
            self._session.rollback()

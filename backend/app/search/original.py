"""The original file of an ingested document, for the viewer's "Original" tab.

Only what was dropped in is served, and only from inside raw/: a stored path
is checked again before a file is opened. Browsers render PDFs and common
images in place; every other type is sent as a download, so nothing a user
uploaded is ever displayed as a web page on the dashboard's origin.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FileRecord

INLINE_TYPES = frozenset(
    {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}
)


def find_record(session: Session, file_id: int | None, path: str | None) -> FileRecord | None:
    if file_id is not None:
        return session.get(FileRecord, file_id)
    if path:
        return session.execute(select(FileRecord).where(FileRecord.path == path)).scalars().first()
    return None


def original_path(stored_path: str, raw_dir: Path) -> Path | None:
    """The file on disk, but only when it really lies inside raw_dir (symlinks
    and ".." are resolved first) and still exists."""
    candidate = Path(stored_path).resolve()
    if not candidate.is_relative_to(raw_dir.resolve()) or not candidate.is_file():
        return None
    return candidate

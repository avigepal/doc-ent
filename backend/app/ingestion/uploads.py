"""Backs the dashboard's "+" attach button: files picked there are saved
into a fixed raw/uploads/ folder, deliberately never into whatever folder
is selected in Scope or any other corpus folder, so ad-hoc attachments
never get mixed into a folder the user curated by hand.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.models import FileRecord
from app.queue_guard import clear_enqueued
from app.search.folder_filter import build_folder_like_patterns

UPLOAD_FOLDER_NAME = "uploads"


def safe_upload_filename(name: str) -> str:
    """Strips any path component and replaces anything outside
    alnum/underscore/hyphen/dot so this can never escape the uploads
    directory via '..' or a path separator. Keeps dots (unlike
    app/export/adhoc.py's safe_filename, which strips them too) so the
    extension survives, e.g. "report.pdf" stays "report.pdf" rather than
    becoming "report-pdf"."""
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.")
    return name or "upload"


def unique_destination(upload_dir: Path, filename: str) -> Path:
    """Appends -1, -2, ... before the extension when filename already
    exists in upload_dir, so a second upload with the same name never
    clobbers the first."""
    dest = upload_dir / filename
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    i = 1
    while dest.exists():
        dest = upload_dir / f"{stem}-{i}{suffix}"
        i += 1
    return dest


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def delete_uploads(session: Session, data_dir: str, file_ids: list[int] | None = None) -> int:
    """Delete uploaded files: all of them (file_ids=None -- a new chat) or
    just the given ones (a chip's X). Removes the database rows (chunks and
    jobs go with them via ON DELETE CASCADE), the file itself, and its
    converted and summary text.

    Only rows under the uploads folder are ever matched, so a file id from
    a corpus folder deletes nothing. Returns how many files were removed."""
    data = Path(data_dir)
    raw_root = data / "raw"

    patterns = build_folder_like_patterns(str(raw_root), [UPLOAD_FOLDER_NAME])
    query = select(FileRecord.id, FileRecord.path).where(or_(*(FileRecord.path.like(p) for p in patterns)))
    if file_ids is not None:
        query = query.where(FileRecord.id.in_(file_ids))
    rows = session.execute(query).all()

    ids = [row[0] for row in rows]
    if ids:
        session.execute(delete(FileRecord).where(FileRecord.id.in_(ids)))
        session.commit()
        clear_enqueued(ids)

    for _file_id, path in rows:
        try:
            relative = Path(path).relative_to(raw_root)
        except ValueError:
            continue
        _remove(Path(path))
        markdown = (data / "converted" / relative).with_suffix(".md")
        _remove(markdown)
        _remove(markdown.with_suffix(".md.json"))
        _remove((data / "summaries" / relative).with_suffix(".md"))

    if file_ids is None:
        # whole-folder clear: also sweep anything on disk the database never
        # knew about (not yet scanned, or written by an in-flight conversion)
        for area in ("raw", "converted", "summaries"):
            folder = data / area / UPLOAD_FOLDER_NAME
            if not folder.is_dir():
                continue
            for child in folder.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    _remove(child)

    return len(ids)

"""Bulk actions for the Documents page: delete files and send them back
through the pipeline. Same split as the other ingestion modules -- the
file-and-database work lives here, the endpoints in main.py are thin.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.ingestion.uploads import UPLOAD_FOLDER_NAME, remove_document_files
from app.models import ChunkRecord, FileRecord
from app.queue_guard import clear_enqueued
from app.search.folder_filter import build_folder_like_patterns


def delete_documents(session: Session, data_dir: str, file_ids: list[int]) -> int:
    """Removes the files from disk (the original, its converted text and its
    summary) and from the database; chunks and jobs go with the file row
    (ON DELETE CASCADE). The original has to go too: the auto-ingest scan
    would otherwise find it again and re-add it. Returns how many were removed."""
    if not file_ids:
        return 0
    rows = session.execute(select(FileRecord.id, FileRecord.path).where(FileRecord.id.in_(file_ids))).all()
    ids = [row[0] for row in rows]
    if not ids:
        return 0

    session.execute(delete(FileRecord).where(FileRecord.id.in_(ids)))
    session.commit()
    clear_enqueued(ids)

    data = Path(data_dir)
    for _file_id, path in rows:
        remove_document_files(data, path)
    return len(ids)


class FolderNotFound(Exception):
    pass


class InvalidFolderName(Exception):
    pass


def clear_folder(session: Session, data_dir: str, name: str) -> int:
    """Empties one top-level corpus folder: every file in it (and its
    subfolders) is deleted from disk and from the index, along with the
    converted text and summaries. The folder itself stays. Returns how many
    indexed files were removed.

    The name must be a single existing folder under raw/. The "uploads"
    folder (a chat's attachments) is refused: a new chat clears that one."""
    if not name or name in (".", "..") or name.startswith(".") or "/" in name or "\\" in name:
        raise InvalidFolderName(name)
    if name == UPLOAD_FOLDER_NAME:
        raise InvalidFolderName(name)

    data = Path(data_dir)
    raw_root = data / "raw"
    folder = raw_root / name
    if not folder.is_dir():
        raise FolderNotFound(name)

    patterns = build_folder_like_patterns(str(raw_root), [name])
    ids = list(session.execute(select(FileRecord.id).where(or_(*(FileRecord.path.like(p) for p in patterns)))).scalars())
    removed = delete_documents(session, data_dir, ids)

    # Sweep what the database never knew about (not scanned yet, or left behind).
    for area in ("raw", "converted", "summaries"):
        target = data / area / name
        if not target.is_dir():
            continue
        for child in target.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink(missing_ok=True)
                except OSError:
                    pass
    return removed


def reprocess_documents(session: Session, file_ids: list[int]) -> list[int]:
    """Sends files back to the start: drops the chunks built from them (they
    would stay searchable otherwise), resets their status so conversion runs
    again, and clears the queue claims so they are queued right away.
    Returns the ids that were reset; the caller enqueues the conversion."""
    if not file_ids:
        return []
    ids = list(session.execute(select(FileRecord.id).where(FileRecord.id.in_(file_ids))).scalars())
    if not ids:
        return []

    session.execute(delete(ChunkRecord).where(ChunkRecord.file_id.in_(ids)))
    # unsupported files stay unsupported: there is nothing to run for them
    session.execute(
        update(FileRecord)
        .where(FileRecord.id.in_(ids), FileRecord.status != "unsupported")
        .values(status="discovered")
    )
    session.commit()
    clear_enqueued(ids)
    return ids

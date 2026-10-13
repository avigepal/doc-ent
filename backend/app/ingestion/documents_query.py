"""Corpus-wide file listing for the Documents page. One call across all
folders — the Documents table would otherwise need N round trips to the
per-folder /folders/{name}/files endpoint.

Folder scoping is a prefix match on the stored path. startswith(autoescape=True)
escapes the characters LIKE treats specially -- the % and _ that folder names
contain, and the backslash of a Windows path (PostgreSQL's default LIKE escape),
which silently broke folder filtering on Windows paths before.
"""

from __future__ import annotations

from sqlalchemy import false, func, or_, select, text
from sqlalchemy.orm import Session

from app.ingestion.uploads import UPLOAD_FOLDER_NAME
from app.models import FileRecord


# Columns the Documents table can sort by (sorted on the server: the table is paged).
SORT_COLUMNS = {
    "path": FileRecord.path,
    "title": FileRecord.title,
    "author": FileRecord.author,
    "page_count": FileRecord.page_count,
    "size_bytes": FileRecord.size_bytes,
    "status": FileRecord.status,
    "discovered_at": FileRecord.discovered_at,
}


def folder_children(session: Session, raw_dir: str, parent: str | None) -> dict:
    """The folders directly under `parent` ("" = raw/), each with how many files it holds
    in all (subfolders included), how many of them are still pending or failed, and
    whether it has subfolders of its own. Also how many files sit right in `parent`.
    One level at a time: the Documents page's tree opens a folder when asked, so a
    corpus with thousands of subfolders is never sent whole."""
    parts = clean_folder_path(parent)
    if ".." in parts:
        return {"parent": "", "folders": [], "files_here": 0}
    normalized_raw = raw_dir.replace("\\", "/").rstrip("/")
    base = normalized_raw + "/" + ("/".join(parts) + "/" if parts else "")

    norm = "replace(path, chr(92), '/')"
    rows = session.execute(
        text(
            f"""
            SELECT first_segment, is_dir, count(*), count(*) FILTER (WHERE status = 'discovered'),
                   count(*) FILTER (WHERE status = 'failed'), bool_or(deeper)
            FROM (
                SELECT status,
                       split_part(rel, '/', 1) AS first_segment,
                       position('/' in rel) > 0 AS is_dir,
                       position('/' in rel) > 0 AND position('/' in substr(rel, position('/' in rel) + 1)) > 0 AS deeper
                FROM (SELECT status, substr({norm}, :blen + 1) AS rel FROM files WHERE left({norm}, :blen) = :base) t
            ) u
            WHERE NOT (is_dir AND :top AND first_segment = :uploads)
            GROUP BY 1, 2
            """
        ),
        {"base": base, "blen": len(base), "top": not parts, "uploads": UPLOAD_FOLDER_NAME},
    ).all()

    prefix = "/".join(parts)
    folders, files_here = [], 0
    for name, is_dir, total, pending, failed, deeper in rows:
        if not is_dir:
            files_here += total
            continue
        folders.append(
            {
                "name": name,
                "path": f"{prefix}/{name}" if prefix else name,
                "total": total,
                "pending": pending,
                "failed": failed,
                "has_subfolders": bool(deeper),
            }
        )
    folders.sort(key=lambda f: f["name"].lower())
    return {"parent": prefix, "folders": folders, "files_here": files_here}


def relative_to_raw(path: str, raw_dir: str) -> str:
    """Trims the raw/ prefix so the UI shows "contracts/lease.pdf" rather
    than the full container path."""
    base = raw_dir.rstrip("/\\")
    for separator in ("/", "\\"):
        prefix = base + separator
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def clean_folder_path(folder: str | None) -> list[str]:
    """A folder path such as "pgt/reports" as its segments; empty for "no folder"."""
    return [part for part in (folder or "").replace("\\", "/").split("/") if part]


def folder_condition(raw_dir: str, folder: str | None):
    """Files anywhere under this folder (subfolders included), whichever separator the
    scan that stored the path used. None for no folder; matches nothing for a path
    that tries to climb out of raw/."""
    parts = clean_folder_path(folder)
    if not parts:
        return None
    if ".." in parts:
        return false()
    base = raw_dir.rstrip("/\\")
    relative = "/".join(parts)
    return or_(
        FileRecord.path.startswith(f"{base}/{relative}/", autoescape=True),
        FileRecord.path.startswith(f"{base}\\{relative.replace('/', chr(92))}\\", autoescape=True),
    )


def document_conditions(raw_dir: str, folder: str | None, status: str | None, search: str | None) -> list:
    """The WHERE clauses for the Documents page's filters, shared by the list and by Delete all.
    A chat's attachments (the "uploads" folder) are never part of the library, so never listed."""
    conditions = [~folder_condition(raw_dir, UPLOAD_FOLDER_NAME)]

    scoped = folder_condition(raw_dir, folder)
    if scoped is not None:
        conditions.append(scoped)

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
    return conditions


def list_documents(
    session: Session,
    *,
    raw_dir: str,
    folder: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
    sort: str = "discovered_at",
    descending: bool = True,
) -> tuple[list[dict], int]:
    conditions = document_conditions(raw_dir, folder, status, search)

    rows_stmt = select(FileRecord)
    count_stmt = select(func.count()).select_from(FileRecord)
    if conditions:
        rows_stmt = rows_stmt.where(*conditions)
        count_stmt = count_stmt.where(*conditions)

    total = session.execute(count_stmt).scalar_one()
    column = SORT_COLUMNS.get(sort, FileRecord.discovered_at)
    ordering = column.desc() if descending else column.asc()
    # rows with no value for the column go last either way; id keeps pages stable
    rows = session.execute(
        rows_stmt.order_by(ordering.nulls_last(), FileRecord.id).limit(limit).offset(offset)
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

"""Pure record -> response-dict shaping for the History page. No DB access
here so it stays unit-testable with plain objects (same split as
app/ingestion/folder_status.py and its _query counterpart).

The list endpoint uses the summary shape and the detail endpoint the full
one: history lists can get long, and shipping every stored answer body
down just to render a list of questions is wasteful.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def query_record_to_summary(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "question": record.question,
        "grounded": record.grounded,
        "source_count": len(record.sources or []),
        "filter_folders": record.filter_folders or [],
        "filter_author": record.filter_author,
        "filter_title": record.filter_title,
        "attached_filenames": record.attached_filenames or [],
        "chat_only": record.chat_only,
        "conversation_id": record.conversation_id,
        "route": getattr(record, "route", "") or "",
        "created_at": _iso(record.created_at),
    }


def generated_file_to_dict(record: Any) -> dict[str, Any]:
    """The shape the dashboard's file card uses (same as the "file" event of
    a live reply), so a restored chat shows the same card."""
    name = record.stored_path.replace("\\", "/").rsplit("/", 1)[-1]
    return {
        "id": record.id,
        "name": name,
        "fmt": record.fmt,
        "size_bytes": record.size_bytes,
        "source": record.source_name or "",
        "download_path": f"/history/exports/{record.id}/download",
    }


def query_record_to_detail(record: Any, files: list[Any] | None = None) -> dict[str, Any]:
    return {
        **query_record_to_summary(record),
        "answer": record.answer,
        "sources": record.sources or [],
        "cross_doc": record.cross_doc,
        "statistical": record.statistical,
        "suggestions": getattr(record, "suggestions", None) or [],
        "files": [generated_file_to_dict(f) for f in files or []],
    }


def export_record_to_dict(record: Any) -> dict[str, Any]:
    # stored_path is deliberately omitted: it's an absolute server path.
    # Downloads go through /history/exports/{id}/download instead.
    return {
        "id": record.id,
        "query_history_id": record.query_history_id,
        "filename": record.filename,
        "fmt": record.fmt,
        "size_bytes": record.size_bytes,
        "created_at": _iso(record.created_at),
    }

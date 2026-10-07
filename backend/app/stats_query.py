"""DB fetching for the Overview page — pairs with the pure shaping in
app/stats.py."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.folders import list_top_level_folders
from app.models import ChunkRecord, ExportHistoryRecord, FileRecord, QueryHistoryRecord
from app.stats import ActivityEvent, build_overview

_ACTIVITY_PER_SOURCE = 10


def get_overview_stats(session: Session, raw_dir: Path) -> dict:
    status_counts = {
        status: count
        for status, count in session.execute(
            select(FileRecord.status, func.count()).group_by(FileRecord.status)
        ).all()
    }

    chunk_count = session.execute(select(func.count()).select_from(ChunkRecord)).scalar_one()
    storage_bytes = session.execute(
        select(func.coalesce(func.sum(FileRecord.size_bytes), 0))
    ).scalar_one()
    query_count = session.execute(select(func.count()).select_from(QueryHistoryRecord)).scalar_one()
    export_count = session.execute(
        select(func.count()).select_from(ExportHistoryRecord)
    ).scalar_one()

    activity: list[ActivityEvent] = []

    for path, discovered_at in session.execute(
        select(FileRecord.path, FileRecord.discovered_at)
        .order_by(FileRecord.discovered_at.desc())
        .limit(_ACTIVITY_PER_SOURCE)
    ).all():
        activity.append(ActivityEvent(type="ingest", label=Path(path).name, at=discovered_at))

    for question, created_at in session.execute(
        select(QueryHistoryRecord.question, QueryHistoryRecord.created_at)
        .order_by(QueryHistoryRecord.created_at.desc())
        .limit(_ACTIVITY_PER_SOURCE)
    ).all():
        activity.append(ActivityEvent(type="query", label=question, at=created_at))

    for filename, fmt, created_at in session.execute(
        select(
            ExportHistoryRecord.filename,
            ExportHistoryRecord.fmt,
            ExportHistoryRecord.created_at,
        )
        .order_by(ExportHistoryRecord.created_at.desc())
        .limit(_ACTIVITY_PER_SOURCE)
    ).all():
        activity.append(ActivityEvent(type="export", label=f"{filename}.{fmt}", at=created_at))

    return build_overview(
        status_counts=status_counts,
        chunk_count=chunk_count,
        storage_bytes=storage_bytes,
        folder_count=len(list_top_level_folders(raw_dir)),
        query_count=query_count,
        export_count=export_count,
        activity=activity,
    )

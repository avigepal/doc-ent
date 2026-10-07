"""Pure aggregation for the Overview page. Takes already-fetched counts
as arguments so it unit-tests without a database — the DB fetching lives
in stats_query.py (same split as app/ingestion/folder_status.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ActivityEvent:
    type: str  # "ingest" | "query" | "export"
    label: str
    at: datetime


def merge_recent_activity(events: list[ActivityEvent], limit: int = 10) -> list[dict[str, Any]]:
    """Interleaves per-source event lists into one newest-first feed."""
    ordered = sorted(events, key=lambda e: e.at, reverse=True)[:limit]
    return [{"type": e.type, "label": e.label, "at": e.at.isoformat()} for e in ordered]


def build_overview(
    *,
    status_counts: dict[str, int],
    chunk_count: int,
    storage_bytes: int,
    folder_count: int,
    query_count: int,
    export_count: int,
    activity: list[ActivityEvent],
) -> dict[str, Any]:
    return {
        "documents": {
            "total": sum(status_counts.values()),
            "discovered": status_counts.get("discovered", 0),
            "converted": status_counts.get("converted", 0),
            "summarized": status_counts.get("summarized", 0),
            "failed": status_counts.get("failed", 0),
        },
        "chunks": chunk_count,
        "storage_bytes": storage_bytes,
        "folders": folder_count,
        "queries_total": query_count,
        "exports_total": export_count,
        "recent_activity": merge_recent_activity(activity),
    }

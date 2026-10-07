"""Pre-run summary — printed/returned before any GPU work starts, so a bad
scan (wrong folder, unexpected file types) is caught before it's expensive
to undo."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.ingestion.walker import ScannedFile


@dataclass(frozen=True)
class ScanSummary:
    total_files: int
    total_bytes: int
    by_queue: dict[str, int]
    by_mime: dict[str, int]


def summarize(files: list[ScannedFile]) -> ScanSummary:
    return ScanSummary(
        total_files=len(files),
        total_bytes=sum(f.size_bytes for f in files),
        by_queue=dict(Counter(f.queue for f in files)),
        by_mime=dict(Counter(f.mime_type for f in files)),
    )

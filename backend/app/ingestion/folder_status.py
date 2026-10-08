"""Per-folder ingestion status for the dashboard's Ask-page sidebar —
shows each top-level raw/ folder with how far its files have gotten
(discovered -> converted -> summarized) and whether anything is
currently running or has failed.
"""

from __future__ import annotations

from dataclasses import dataclass


def top_level_folder(path: str, raw_dir: str) -> str | None:
    """Extract the first path segment under raw_dir, handling both
    forward-slash (Linux/Docker) and backslash (Windows local dev)
    separators. Returns None for files directly in raw/ (no folder)."""
    normalized_path = path.replace("\\", "/")
    normalized_base = raw_dir.replace("\\", "/").rstrip("/") + "/"

    if not normalized_path.startswith(normalized_base):
        return None

    remainder = normalized_path[len(normalized_base):]
    parts = [p for p in remainder.split("/") if p]
    if len(parts) < 2:  # just a filename, not inside a subfolder
        return None
    return parts[0]


@dataclass(frozen=True)
class FolderStatus:
    name: str
    total_files: int
    discovered: int
    converted: int
    summarized: int
    processing: bool
    has_failures: bool
    # Files that can never be converted (.exe, .dll, ...). Not part of
    # total_files, so a folder containing one can still reach "all done".
    unsupported: int = 0


def _bucket_for(buckets: dict[str, dict], name: str) -> dict:
    return buckets.setdefault(
        name,
        {
            "total": 0,
            "discovered": 0,
            "converted": 0,
            "summarized": 0,
            "unsupported": 0,
            "processing": False,
            "has_failures": False,
        },
    )


def build_folder_statuses(
    raw_dir: str,
    files: list[tuple[int, str, str]],  # (file_id, path, status)
    running_file_ids: set[int],
    failed_file_ids: set[int],
) -> list[FolderStatus]:
    """Pure aggregation — the DB queries that produce `files`,
    `running_file_ids`, `failed_file_ids` live in app/ingestion/
    folder_status_query.py (needs a live DB, not unit-tested); this part
    is the actual grouping logic and is fully testable without one."""
    buckets: dict[str, dict] = {}

    for file_id, path, status in files:
        folder = top_level_folder(path, raw_dir)
        if folder is None:
            continue
        b = _bucket_for(buckets, folder)
        if status == "unsupported":
            b["unsupported"] += 1
            continue
        b["total"] += 1
        if status in b:
            b[status] += 1
        if file_id in running_file_ids:
            b["processing"] = True
        if file_id in failed_file_ids:
            b["has_failures"] = True

    return [
        FolderStatus(
            name=name,
            total_files=b["total"],
            discovered=b["discovered"],
            converted=b["converted"],
            summarized=b["summarized"],
            processing=b["processing"],
            has_failures=b["has_failures"],
            unsupported=b["unsupported"],
        )
        for name, b in sorted(buckets.items())
    ]

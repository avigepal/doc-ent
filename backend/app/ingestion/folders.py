"""Lists the top-level folders under raw/ so the dashboard can offer a
"search only these folders" picker. Reads the filesystem directly (not
the DB) so it reflects folders that exist even before a scan has run.

Top-level only, matching the plan's recommended default grouping
strategy (folder-based) — if deeper, per-subfolder scoping turns out to
be needed later, this is the one place to extend.
"""

from __future__ import annotations

from pathlib import Path

from app.ingestion.uploads import UPLOAD_FOLDER_NAME


def list_top_level_folders(raw_dir: Path) -> list[str]:
    """The uploads folder holds the chats' attachments, not a corpus folder
    to browse or scope a search to, so it is left out."""
    if not raw_dir.exists():
        return []
    return sorted(
        p.name
        for p in raw_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name != UPLOAD_FOLDER_NAME
    )

"""Which database file records no longer have a file on disk.

Scans only ever added/updated records, so deleting a file from raw/ left
its record (and any failed job on it) behind — the folder's status icon
then flagged a file that no longer exists.
"""

from __future__ import annotations


def paths_to_prune(known_paths: set[str], on_disk_paths: set[str]) -> set[str]:
    """Records whose file has disappeared. An empty scan prunes nothing:
    raw/ may be a network mount (see app/tasks/auto_ingest.py) that is
    briefly unavailable, and "the share is down" must not look identical
    to "every file was deleted"."""
    if not on_disk_paths:
        return set()
    return known_paths - on_disk_paths

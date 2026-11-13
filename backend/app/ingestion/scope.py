"""Which part of raw/ one ingest step covers: a single folder (and everything
under it), or the whole of raw/ when no folder is given.

Folder names come from the browser, so they are checked before any path is
built from them: nothing may climb out of raw/ (".."), start a hidden folder
(".incoming" is a staging area, not part of the library), or name a drive.
"""

from __future__ import annotations

import os
from pathlib import Path


class BadFolder(ValueError):
    pass


def clean_folder(folder: str) -> str:
    """"" for the whole of raw/, else the folder as "a" or "a/b"."""
    folder = folder.strip().strip("/").replace("\\", "/")
    if not folder:
        return ""
    for part in folder.split("/"):
        if part in ("", ".", "..") or part.startswith(".") or ":" in part:
            raise BadFolder(f"not a folder name: {folder!r}")
    return folder


def folder_dir(raw_dir: Path, folder: str) -> Path:
    """raw/<folder>, or raw/ itself for the whole library."""
    folder = clean_folder(folder)
    return raw_dir.joinpath(*folder.split("/")) if folder else raw_dir


def scope_prefix(raw_dir: Path, folder: str) -> str:
    """The text every stored path in this scope starts with: the folder's
    own path plus a separator, so "raw/sales" never matches "raw/sales2"."""
    return str(folder_dir(raw_dir, folder)) + os.sep


def in_scope(paths: set[str], prefix: str) -> set[str]:
    return {p for p in paths if p.startswith(prefix)}

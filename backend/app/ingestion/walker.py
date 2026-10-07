"""
Manifest walker — phase 1.

Walks a root directory, hashes each file (sha256), classifies it by
content-sniffed mime type (python-magic), and routes it to a conversion
queue via the handler registry. Yields one ScannedFile per file; callers
decide what to do with the stream (upsert into Postgres, print a summary,
etc.) so this module stays testable without a database.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app.handlers.registry import route_mime

try:
    import magic

    _MAGIC = magic.Magic(mime=True)
except Exception:  # pragma: no cover - exercised only when libmagic is unavailable
    _MAGIC = None


@dataclass(frozen=True)
class ScannedFile:
    path: str
    sha256: str
    mime_type: str
    size_bytes: int
    queue: str


def _hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _detect_mime(path: Path) -> str:
    if _MAGIC is not None:
        try:
            return _MAGIC.from_file(str(path))
        except Exception:
            pass
    # fallback: best-effort guess by extension, kept deliberately crude —
    # real classification happens via libmagic; this only covers dev
    # environments where libmagic isn't installed.
    import mimetypes

    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def scan_directory(root: Path) -> Iterator[ScannedFile]:
    root = Path(root)
    if not root.exists():
        return

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue

        mime_type = _detect_mime(path)
        queue = route_mime(mime_type, filename=path.name)

        yield ScannedFile(
            path=str(path),
            sha256=_hash_file(path),
            mime_type=mime_type,
            size_bytes=path.stat().st_size,
            queue=queue,
        )

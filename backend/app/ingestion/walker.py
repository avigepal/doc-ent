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

from app.handlers.registry import UNSUPPORTED, route_mime

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
    mtime_ns: int = 0


@dataclass(frozen=True)
class KnownFile:
    """What a previous scan recorded about a file. When size and mtime still
    match, the file is assumed unchanged and isn't read again."""

    size_bytes: int
    mtime_ns: int | None
    mime_type: str
    sha256: str


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


def scan_file(path: Path, known: KnownFile | None = None) -> ScannedFile:
    """Hash + classify one file. Split out of scan_directory so a handful of
    freshly uploaded files can be registered without re-walking (and
    re-hashing) the entire corpus.

    `known` is the previous scan's record of this file: if its size and
    mtime are unchanged the stored hash and type are reused, so the
    auto-ingest tick (every minute) costs a stat() per file instead of
    reading every byte of the corpus each time."""
    path = Path(path)
    stat = path.stat()

    if known is not None and known.mtime_ns == stat.st_mtime_ns and known.size_bytes == stat.st_size:
        # routing is recomputed from the stored type, so a routing change
        # in a new release still takes effect on unchanged files
        queue = route_mime(known.mime_type, filename=path.name)
        if queue == UNSUPPORTED or known.sha256:
            return ScannedFile(
                path=str(path),
                sha256="" if queue == UNSUPPORTED else known.sha256,
                mime_type=known.mime_type,
                size_bytes=stat.st_size,
                queue=queue,
                mtime_ns=stat.st_mtime_ns,
            )

    mime_type = _detect_mime(path)
    queue = route_mime(mime_type, filename=path.name)

    return ScannedFile(
        path=str(path),
        # Unsupported files are never converted, so skip reading them in
        # full just to hash them (could be multi-GB installers/videos).
        sha256="" if queue == UNSUPPORTED else _hash_file(path),
        mime_type=mime_type,
        size_bytes=stat.st_size,
        queue=queue,
        mtime_ns=stat.st_mtime_ns,
    )


def scan_directory(root: Path, known: dict[str, KnownFile] | None = None) -> Iterator[ScannedFile]:
    root = Path(root)
    if not root.exists():
        return

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        yield scan_file(path, known.get(str(path)) if known else None)

"""Phase 2 plumbing: run a conversion backend against one file and write
its output to converted/, preserving the file's path relative to raw/ so
converted/ mirrors raw/'s layout.

Kept separate from app/tasks/convert.py (which adds Celery + DB bookkeeping)
so this can be unit-tested with a fake backend and no database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.conversion.backends import ConversionBackend


class EmptyConversionError(Exception):
    """The backend ran without error but produced no text. Treated as a
    failure: a "converted" file with an empty document can never be
    searched, and it used to pass silently (0-byte .md, indexed as 0
    chunks). Retrying can't help -- the same file gives the same result."""


@dataclass(frozen=True)
class StoredConversion:
    markdown_path: Path
    metadata_path: Path
    char_count: int
    engine_metadata: dict


def convert_and_store(
    raw_root: Path,
    file_path: Path,
    converted_root: Path,
    backend: ConversionBackend,
    extra_metadata: dict,
) -> StoredConversion:
    result = backend.convert(file_path)
    if not result.markdown.strip():
        raise EmptyConversionError(
            f"{file_path.name}: conversion produced no text (scanned or image-only file, or unreadable)"
        )

    relative = file_path.relative_to(raw_root)
    markdown_path = (converted_root / relative).with_suffix(".md")
    metadata_path = markdown_path.with_suffix(".md.json")

    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(result.markdown, encoding="utf-8")

    metadata = {
        **extra_metadata,
        "source_path": str(file_path),
        "engine": result.engine,
        "engine_metadata": result.engine_metadata,
        "char_count": len(result.markdown),
        "converted_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return StoredConversion(
        markdown_path=markdown_path,
        metadata_path=metadata_path,
        char_count=len(result.markdown),
        engine_metadata=result.engine_metadata,
    )

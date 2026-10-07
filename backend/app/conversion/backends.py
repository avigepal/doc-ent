"""Conversion backends — each wraps one conversion engine behind the same
tiny interface (`convert(path) -> ConversionResult`) so the pipeline and
its tests never depend on a specific engine being installed.

Docling is the primary backend for convert_fast. Marker (hard PDFs) and
Apache Tika (generic catch-all) are planned fallbacks — not wired in yet;
add them here as additional classes with the same interface and have the
task try them in order on failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ConversionResult:
    markdown: str
    engine: str
    engine_metadata: dict = field(default_factory=dict)


class ConversionBackend(Protocol):
    def convert(self, path: Path) -> ConversionResult: ...


class DoclingBackend:
    """Wraps docling.document_converter.DocumentConverter. Imported lazily
    so this module (and anything that imports it) doesn't require docling
    and its model downloads just to run unit tests against the pipeline
    plumbing."""

    def __init__(self) -> None:
        self._converter = None

    def _get_converter(self):
        if self._converter is None:
            from docling.document_converter import DocumentConverter

            self._converter = DocumentConverter()
        return self._converter

    def convert(self, path: Path) -> ConversionResult:
        converter = self._get_converter()
        result = converter.convert(str(path))
        markdown = result.document.export_to_markdown()
        # "title" defaults to the filename — Docling doesn't reliably expose
        # an embedded document title across formats via its basic API, so
        # this is a safe, always-available fallback rather than a guess at
        # fragile library internals. page_count IS reliably available.
        metadata: dict = {"title": path.stem}
        try:
            metadata["page_count"] = len(result.document.pages)
        except Exception:
            pass
        return ConversionResult(markdown=markdown, engine="docling", engine_metadata=metadata)


class PlainTextBackend:
    """For formats that are already plain text (text/plain, text/csv) and
    need no real conversion — Docling rejects these outright ("Input
    document does not match any allowed format"), confirmed live against
    a real .txt file. Just reads the file as-is."""

    def convert(self, path: Path) -> ConversionResult:
        text = path.read_text(encoding="utf-8", errors="replace")
        return ConversionResult(markdown=text, engine="plaintext", engine_metadata={"title": path.stem})


# Shared singletons + mime routing, reused by both the ingestion pipeline
# (app/tasks/convert.py) and ad-hoc dashboard uploads (app/search/
# adhoc_upload.py) so "how do we convert a file" lives in exactly one place.
docling_backend = DoclingBackend()
plaintext_backend = PlainTextBackend()

# Docling rejects formats that are already plain text outright ("Input
# document does not match any allowed format" — confirmed live against a
# real .txt file), so route those to PlainTextBackend instead.
_PLAINTEXT_MIMES = {"text/plain", "text/csv"}


def resolve_conversion_backend(mime_type: str) -> ConversionBackend:
    return plaintext_backend if mime_type in _PLAINTEXT_MIMES else docling_backend

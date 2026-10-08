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

from app.handlers.registry import is_plain_text


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
        self._pdfium_converter = None

    def _build_converter(self, pdfium: bool = False):
        """The Docling converter. `pdfium=True` swaps its PDF reader from the
        default docling-parse to pypdfium2 -- see convert()."""
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
        from docling.document_converter import DocumentConverter, PdfFormatOption

        from app.config import settings

        options = PdfPipelineOptions()
        options.do_ocr = settings.docling_ocr
        options.do_table_structure = settings.docling_tables
        options.table_structure_options.mode = (
            TableFormerMode.ACCURATE if settings.docling_table_mode.lower() == "accurate" else TableFormerMode.FAST
        )
        if settings.docling_page_batch_size > 0:
            # Docling 2.9 runs its layout/table/OCR models on this many
            # pages at a time (default 4). Bigger batches keep a GPU
            # busier; the cost is VRAM.
            from docling.datamodel.settings import settings as docling_settings

            docling_settings.perf.page_batch_size = settings.docling_page_batch_size

        pdf_option = PdfFormatOption(pipeline_options=options)
        if pdfium:
            from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend

            pdf_option = PdfFormatOption(pipeline_options=options, backend=PyPdfiumDocumentBackend)
        return DocumentConverter(format_options={InputFormat.PDF: pdf_option})

    def _get_converter(self):
        if self._converter is None:
            self._converter = self._build_converter()
        return self._converter

    def _get_pdfium_converter(self):
        if self._pdfium_converter is None:
            self._pdfium_converter = self._build_converter(pdfium=True)
        return self._pdfium_converter

    def convert(self, path: Path) -> ConversionResult:
        from docling.datamodel.base_models import ConversionStatus

        converter = self._get_converter()
        result = converter.convert(str(path))
        markdown = result.document.export_to_markdown()
        engine = "docling"

        # Docling's default PDF reader (docling-parse, a C++ library) fails on
        # some perfectly good PDFs -- "Page N failed to parse" -- and then
        # yields an empty or partial document, which used to be stored as a
        # successful (empty) conversion. When that happens, redo the file with
        # pypdfium2 as the reader (same layout and table models on top) and
        # keep whichever result has more text.
        if path.suffix.lower() == ".pdf" and (not markdown.strip() or result.status != ConversionStatus.SUCCESS):
            retry = self._get_pdfium_converter().convert(str(path))
            retry_markdown = retry.document.export_to_markdown()
            if len(retry_markdown.strip()) > len(markdown.strip()):
                result, markdown, engine = retry, retry_markdown, "docling-pdfium"
        # "title" defaults to the filename — Docling doesn't reliably expose
        # an embedded document title across formats via its basic API, so
        # this is a safe, always-available fallback rather than a guess at
        # fragile library internals. page_count IS reliably available.
        metadata: dict = {"title": path.stem}
        try:
            metadata["page_count"] = len(result.document.pages)
        except Exception:
            pass
        return ConversionResult(markdown=markdown, engine=engine, engine_metadata=metadata)


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


def resolve_conversion_backend(mime_type: str, filename: str | None = None) -> ConversionBackend:
    if mime_type.startswith("image/"):
        # lazy: vision_backend imports this module (ConversionResult)
        from app.conversion.vision_backend import vision_backend

        return vision_backend

    # Docling rejects text formats outright, so anything already text (see
    # registry.is_plain_text) is read as-is. `filename` lets a log or YAML file
    # that libmagic could only call "application/octet-stream" still be read.
    return plaintext_backend if is_plain_text(mime_type, filename) else docling_backend

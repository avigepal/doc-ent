"""Ad-hoc file upload + ask — the dashboard's ChatGPT/OpenWebUI-style "+"
attach button. Converts uploaded files on the spot (same backends as the
ingestion pipeline) and feeds them straight into run_query() as
already-maximally-relevant chunks (score=1.0), bypassing pgvector entirely
— these files were never ingested into the corpus, they only exist for
this one question.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.conversion.backends import resolve_conversion_backend
from app.conversion.email_backend import EmailArchiveBackend
from app.search.retrieval import RetrievedChunk
from app.summarization.chunker import chunk_markdown

_EMAIL_EXTENSIONS = {".eml", ".mbox", ".msg", ".pst"}
_email_backend = EmailArchiveBackend()

try:
    import magic

    _magic = magic.Magic(mime=True)
except Exception:  # pragma: no cover - exercised only when libmagic is unavailable
    _magic = None


def detect_mime(content: bytes, fallback: str = "application/octet-stream") -> str:
    if _magic is not None:
        try:
            return _magic.from_buffer(content)
        except Exception:
            pass
    return fallback


def _matches(filter_value: str | None, metadata_value: str | None) -> bool:
    """Case-insensitive substring match, same semantics as the corpus
    path's FileRecord.author/title.ilike(f"%{value}%") — see
    app/search/pgvector_retrieval.py. No filter given always matches."""
    if not filter_value:
        return True
    return bool(metadata_value) and filter_value.lower() in metadata_value.lower()


def convert_upload_to_chunks(
    filename: str,
    content: bytes,
    content_type: str | None = None,
    author_filter: str | None = None,
    title_filter: str | None = None,
) -> list[RetrievedChunk]:
    """Writes the upload to a temp file (conversion backends operate on
    paths, not bytes), converts it, chunks the result, and returns it as
    RetrievedChunks scored 1.0 (it was explicitly provided, so it's always
    "relevant" — there's no retrieval threshold to clear).

    author_filter/title_filter give the "+" attach flow the same
    author/title filtering the ingested-corpus search already has: the
    conversion backends already extract this document-intrinsic metadata
    (same as app/tasks/convert.py:_apply_document_metadata does for
    ingestion) — this just checks it against the filter instead of storing
    it, since ad-hoc uploads never get a FileRecord row. A file that
    doesn't match returns no chunks, effectively excluding it from the
    answer without erroring the whole request."""
    suffix = Path(filename).suffix.lower()
    mime_type = detect_mime(content, fallback=content_type or "application/octet-stream")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        backend = _email_backend if suffix in _EMAIL_EXTENSIONS else resolve_conversion_backend(mime_type, filename)
        result = backend.convert(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    if author_filter or title_filter:
        meta = result.engine_metadata or {}
        if not _matches(author_filter, meta.get("author")) or not _matches(title_filter, meta.get("title")):
            return []

    chunks = chunk_markdown(result.markdown)
    if not chunks:
        return [RetrievedChunk(file_path=filename, heading="", text=result.markdown, score=1.0)]
    return [
        RetrievedChunk(file_path=filename, heading=c.heading, text=c.text, score=1.0)
        for c in chunks
    ]

"""
Handler registry: maps a detected mime type (from python-magic) to the
Celery queue a file should be routed to for conversion.

Queues (match docker-compose.yml service names):
  convert_fast           - Docling: PDF, Office docs, HTML, and the generic catch-all
  convert_ocr            - reserved for explicit OCR-only routing (phase 2 decides
                            fast vs. OCR internally via a text-density probe; this
                            queue exists for callers that already know OCR is needed)
  convert_vision         - images -> Qwen2.5-VL captioning
  convert_email_archive  - .mbox / .eml / .msg / .pst
"""

CONVERT_FAST = "convert_fast"
CONVERT_OCR = "convert_ocr"
CONVERT_VISION = "convert_vision"
CONVERT_EMAIL_ARCHIVE = "convert_email_archive"

_MIME_TO_QUEUE: dict[str, str] = {
    # documents / office / fast path (Docling primary, Marker/Tika fallbacks happen inside the task)
    "application/pdf": CONVERT_FAST,
    "application/msword": CONVERT_FAST,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": CONVERT_FAST,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": CONVERT_FAST,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": CONVERT_FAST,
    "application/vnd.ms-excel": CONVERT_FAST,
    "application/vnd.ms-powerpoint": CONVERT_FAST,
    "text/html": CONVERT_FAST,
    "text/plain": CONVERT_FAST,
    "text/csv": CONVERT_FAST,
    # images
    "image/png": CONVERT_VISION,
    "image/jpeg": CONVERT_VISION,
    "image/tiff": CONVERT_VISION,
    "image/bmp": CONVERT_VISION,
    "image/webp": CONVERT_VISION,
    # email / archives
    "application/mbox": CONVERT_EMAIL_ARCHIVE,
    "message/rfc822": CONVERT_EMAIL_ARCHIVE,
    "application/vnd.ms-outlook": CONVERT_EMAIL_ARCHIVE,
}

# Fallback by file extension when magic reports something too generic
# (e.g. python-magic often reports .pst/.mbox as application/octet-stream).
_EXTENSION_TO_QUEUE: dict[str, str] = {
    ".pst": CONVERT_EMAIL_ARCHIVE,
    ".mbox": CONVERT_EMAIL_ARCHIVE,
    ".eml": CONVERT_EMAIL_ARCHIVE,
    ".msg": CONVERT_EMAIL_ARCHIVE,
}


def route_mime(mime_type: str, filename: str | None = None) -> str:
    """Return the Celery queue name a file with this mime type (and optional
    filename, for extension-based fallback) should be routed to.

    Unknown/unrecognized mime types fall back to convert_fast, which uses
    Apache Tika as its own catch-all converter.
    """
    if mime_type in _MIME_TO_QUEUE:
        return _MIME_TO_QUEUE[mime_type]

    if filename:
        for ext, queue in _EXTENSION_TO_QUEUE.items():
            if filename.lower().endswith(ext):
                return queue

    return CONVERT_FAST

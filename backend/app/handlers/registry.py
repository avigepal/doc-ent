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
  unsupported            - everything else (.exe, .dll, archives, audio, ...). Never
                            queued: the scan records the file with status "unsupported"
                            so it is visible in the dashboard but not retried forever.
"""

CONVERT_FAST = "convert_fast"
CONVERT_OCR = "convert_ocr"
CONVERT_VISION = "convert_vision"
CONVERT_EMAIL_ARCHIVE = "convert_email_archive"
UNSUPPORTED = "unsupported"

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

_GENERIC_MIMES = {"application/octet-stream", "application/zip", "application/x-ole-storage", "application/x-cfb"}

# Text formats that need no conversion -- the file IS the text. Docling
# rejects them ("Input document does not match any allowed format"), so they
# are read as-is by PlainTextBackend. text/* (plain, csv, markdown, source
# code, xml, yaml/toml when magic knows them...) is covered by is_plain_text;
# these are the ones libmagic reports under application/*.
_TEXT_MIMES = {
    "application/json",
    "application/x-ndjson",
    "application/jsonl",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "application/toml",
    "application/x-toml",
}

# libmagic can't identify YAML, TOML or logs (they come back as text/plain,
# or as a generic binary type when a log has odd bytes), so for a generic
# type the extension decides. Deliberately NOT here: .env (secrets).
_TEXT_EXTENSIONS = (
    ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".toml", ".log",
    ".ini", ".cfg", ".conf", ".properties", ".txt", ".csv",
)


def is_plain_text(mime_type: str, filename: str | None = None) -> bool:
    """True for files that are already text and are read as-is. The
    extension is only trusted for a generic mime type, never to rescue an
    executable renamed to .log."""
    if mime_type == "text/html":  # Docling handles (and needs to strip) HTML
        return False
    if mime_type.startswith("text/") or mime_type in _TEXT_MIMES:
        return True
    return bool(filename) and mime_type in _GENERIC_MIMES and filename.lower().endswith(_TEXT_EXTENSIONS)

_DOCUMENT_EXTENSIONS = (".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".pdf", ".txt", ".csv", ".html", ".htm")


def route_mime(mime_type: str, filename: str | None = None) -> str:
    """Return the Celery queue name a file with this mime type (and optional
    filename, for extension-based fallback) should be routed to.

    Unrecognized types return UNSUPPORTED: Docling rejects them anyway, and
    routing them to convert_fast made the pipeline retry them forever.
    """
    if mime_type in _MIME_TO_QUEUE:
        return _MIME_TO_QUEUE[mime_type]

    if filename:
        for ext, queue in _EXTENSION_TO_QUEUE.items():
            if filename.lower().endswith(ext):
                return queue

    # Office files are zip containers (or OLE blobs); older libmagic builds
    # report them as a generic type. Trust the extension only for those
    # generic types -- never to rescue an .exe renamed to .pdf.
    if filename and mime_type in _GENERIC_MIMES:
        for ext in _DOCUMENT_EXTENSIONS:
            if filename.lower().endswith(ext):
                return CONVERT_FAST

    # Text in any form (text/*, JSON, YAML, TOML, logs...) is readable as-is.
    if is_plain_text(mime_type, filename):
        return CONVERT_FAST

    return UNSUPPORTED

"""Phase 2 — conversion pipeline.

convert_fast (Docling) and convert_email_archive (mailbox/extract-msg/
readpst) are implemented, both via the shared convert_and_store pipeline.
convert_ocr / convert_vision still need their routing probes wired up
(Tesseract text-density check, then Docling OCR vs. a llama-server vision
call) before they can run.
"""

from datetime import datetime
from pathlib import Path
from typing import Callable

from celery import Task

from app.celery_app import celery_app
from app.config import settings
from app.conversion.backends import ConversionBackend
from app.conversion.backends import resolve_conversion_backend as _convert_fast_backend
from app.conversion.email_backend import EmailArchiveBackend
from app.conversion.pipeline import convert_and_store
from app.db import SessionLocal
from app.models import FileRecord, JobRecord

_email_archive = EmailArchiveBackend()


def _apply_document_metadata(file_record: FileRecord, engine_metadata: dict) -> None:
    """Pulls the well-known optional keys (title/author/page_count/
    doc_created_at) out of a backend's engine_metadata dict and onto the
    FileRecord — whatever a given backend didn't determine just stays
    unset (None), never overwritten with a placeholder."""
    title = engine_metadata.get("title")
    if title:
        file_record.title = title

    author = engine_metadata.get("author")
    if author:
        file_record.author = author

    page_count = engine_metadata.get("page_count")
    if page_count is not None:
        file_record.page_count = page_count

    doc_created_at = engine_metadata.get("doc_created_at")
    if doc_created_at:
        try:
            file_record.doc_created_at = datetime.fromisoformat(doc_created_at)
        except (TypeError, ValueError):
            pass


def _run_conversion_job(
    task: Task,
    file_id: int,
    job_type: str,
    backend: ConversionBackend | None = None,
    backend_for: Callable[[str], ConversionBackend] | None = None,
) -> None:
    """Exactly one of `backend` (fixed) or `backend_for` (chosen from the
    file's mime type once it's loaded) must be given."""
    session = SessionLocal()
    job = JobRecord(file_id=file_id, job_type=job_type, state="running")
    session.add(job)
    session.commit()

    try:
        file_record = session.get(FileRecord, file_id)
        if file_record is None:
            job.state = "failed"
            job.error = f"file_id {file_id} not found"
            session.commit()
            return

        raw_root = Path(settings.data_dir) / "raw"
        converted_root = Path(settings.data_dir) / "converted"
        resolved_backend = backend_for(file_record.mime_type) if backend_for else backend

        stored = convert_and_store(
            raw_root=raw_root,
            file_path=Path(file_record.path),
            converted_root=converted_root,
            backend=resolved_backend,
            extra_metadata={"sha256": file_record.sha256, "mime_type": file_record.mime_type},
        )
        _apply_document_metadata(file_record, stored.engine_metadata)

        file_record.status = "converted"
        job.state = "done"
        session.commit()
    except Exception as exc:
        job.state = "failed"
        job.error = str(exc)
        job.retries = (job.retries or 0) + 1
        session.commit()
        raise task.retry(exc=exc)
    finally:
        session.close()


@celery_app.task(name="app.tasks.convert.convert_fast", bind=True, max_retries=3, default_retry_delay=30)
def convert_fast(self, file_id: int) -> None:
    _run_conversion_job(self, file_id, "convert_fast", backend_for=_convert_fast_backend)


@celery_app.task(name="app.tasks.convert.convert_email_archive", bind=True, max_retries=3, default_retry_delay=30)
def convert_email_archive(self, file_id: int) -> None:
    _run_conversion_job(self, file_id, "convert_email_archive", _email_archive)


@celery_app.task(name="app.tasks.convert.convert_ocr")
def convert_ocr(file_id: int) -> None:
    raise NotImplementedError("phase 2: wire up Tesseract probe + Docling OCR/Marker fallback")


@celery_app.task(name="app.tasks.convert.convert_vision")
def convert_vision(file_id: int) -> None:
    raise NotImplementedError("phase 2: wire up Qwen2.5-VL captioning via llama-server")

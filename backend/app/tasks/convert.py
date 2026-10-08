"""Phase 2 — conversion pipeline.

convert_fast (Docling) and convert_email_archive (mailbox/extract-msg/
readpst) are implemented, both via the shared convert_and_store pipeline.
convert_vision (standalone images, via the llama-server vision model) is
implemented too -- see app/conversion/vision_backend.py. convert_ocr still
needs its routing probe wired up (Tesseract text-density check) before it
can run; Docling already OCRs scanned PDFs inside convert_fast.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

import httpx
from celery import Task
from celery.signals import worker_process_init

from app.celery_app import celery_app
from app.config import settings
from app.conversion.backends import ConversionBackend
from app.conversion.backends import resolve_conversion_backend as _convert_fast_backend
from app.conversion.email_backend import EmailArchiveBackend
from app.conversion.pipeline import EmptyConversionError, convert_and_store
from app.conversion.vision_backend import vision_backend
from app.db import SessionLocal
from app.models import FileRecord, JobRecord

logger = logging.getLogger(__name__)

_email_archive = EmailArchiveBackend()


@worker_process_init.connect
def _warm_up_docling(**_) -> None:
    """Load Docling's models when a converter worker process starts instead
    of on the first file -- the first conversion otherwise pays the whole
    model-load cost (tens of seconds, plus CUDA init), which is exactly the
    delay a user sees after pressing upload. Opt-in per service via
    WARM_UP_DOCLING=1 so api/summarize/etc. workers (which never run
    Docling) don't spend memory on it. Best-effort: a failure here just
    means the first file pays the cost, as before."""
    if os.environ.get("WARM_UP_DOCLING") != "1":
        return
    try:
        from docling.datamodel.base_models import InputFormat

        from app.conversion.backends import docling_backend

        docling_backend._get_converter().initialize_pipeline(InputFormat.PDF)
        # warning level on purpose: Celery workers default to --loglevel=WARNING,
        # so an info line would never show and you couldn't tell warm-up ran.
        logger.warning("docling pipeline warmed up at worker start")
    except Exception:
        logger.warning("docling warm-up failed; first conversion will load models", exc_info=True)


def _task_queue(task: Task) -> str | None:
    """The queue this task run was delivered on (its worker)."""
    return (getattr(task.request, "delivery_info", None) or {}).get("routing_key")


def _queue_indexing(file_id: int, queue: str | None = None) -> None:
    """Index the file the moment it's converted, so it becomes searchable
    without waiting for the next auto-ingest tick. A file converted on the
    upload lane is indexed on that lane too (no waiting behind other work).
    Best-effort: if the broker hiccups, the conversion is still good and
    the timer's run_index_enqueue picks the file up (it indexes anything
    converted that has no chunks yet)."""
    try:
        from app.queue_guard import UPLOAD_QUEUE, enqueue_once
        from app.tasks.index import index_file

        enqueue_once(index_file, file_id, queue=UPLOAD_QUEUE if queue == UPLOAD_QUEUE else None)
    except Exception:
        logger.warning("could not queue indexing for file %s; auto-ingest will", file_id, exc_info=True)


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
    backend_for: Callable[..., ConversionBackend] | None = None,
) -> None:
    """Exactly one of `backend` (fixed) or `backend_for` (chosen from the
    file's mime type once it's loaded) must be given."""
    session = SessionLocal()

    # Same duplicate-queueing problem as summarize_file: the auto-ingest
    # timer re-queues every "discovered" file each tick while conversion
    # (minutes for a big PDF) is still running. Skip if it already finished.
    existing = session.get(FileRecord, file_id)
    # also skip a file that no longer exists (an upload cleared by a new
    # chat while its task was still queued)
    if existing is None or existing.status in ("converted", "summarized"):
        session.close()
        return

    job = JobRecord(file_id=file_id, job_type=job_type, state="running", queue=_task_queue(task))
    session.add(job)
    session.commit()

    file_record = None
    try:
        file_record = session.get(FileRecord, file_id)
        if file_record is None:
            job.state = "failed"
            job.error = f"file_id {file_id} not found"
            session.commit()
            return

        raw_root = Path(settings.data_dir) / "raw"
        converted_root = Path(settings.data_dir) / "converted"
        resolved_backend = (
            backend_for(file_record.mime_type, Path(file_record.path).name) if backend_for else backend
        )

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
        _queue_indexing(file_id, _task_queue(task))
    except EmptyConversionError as exc:
        # Deterministic: park the file as failed right away instead of
        # re-running a multi-minute conversion three more times.
        job.state = "failed"
        job.error = str(exc)
        job.retries = (job.retries or 0) + 1
        if file_record is not None:
            file_record.status = "failed"
        session.commit()
        return
    except httpx.TransportError as exc:
        # The LLM endpoint is unreachable or timed out (vision conversion).
        # That's "not ready yet", not a bad file: keep retrying with backoff
        # instead of using up the retry budget and parking the file as
        # failed -- same policy as summarize_file.
        job.error = str(exc)
        job.retries = (job.retries or 0) + 1
        session.commit()
        delay = min(30 * (2**task.request.retries), 300)
        raise task.retry(exc=exc, countdown=delay, max_retries=None)
    except Exception as exc:
        job.state = "failed"
        job.error = str(exc)
        job.retries = (job.retries or 0) + 1
        # Out of retries: park the file as "failed" so the next auto-ingest
        # cycle (which only enqueues "discovered" files) doesn't start the
        # whole retry sequence again, forever. Fixing the file changes its
        # hash; a rescan then puts it back (see run_scan).
        if task.request.retries >= task.max_retries and file_record is not None:
            file_record.status = "failed"
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


@celery_app.task(name="app.tasks.convert.convert_vision", bind=True, max_retries=3, default_retry_delay=30)
def convert_vision(self, file_id: int) -> None:
    _run_conversion_job(self, file_id, "convert_vision", vision_backend)

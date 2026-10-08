"""Phase 4 — indexing: chunk each converted file, embed the chunks, and
store them in the `chunks` table that retrieval searches.

Runs on the `correlate` queue (which already has access to the embedding
server) so no new worker service is needed. Like summarize_file, an
unreachable embedding endpoint is "not ready yet" and retries with
backoff instead of failing the job.

A file counts as indexed when it has chunk rows -- there's deliberately
no new files.status value, since folder/progress/stats views all key off
the existing converted/summarized statuses.
"""

from pathlib import Path

import httpx
from sqlalchemy import delete, select, text

from app.celery_app import celery_app
from app.config import settings
from app.db import SessionLocal
from app.models import ChunkRecord, FileRecord, JobRecord
from app.search.embedding_client import EmbeddingClient
from app.search.indexing import build_embedding_input
from app.summarization.chunker import chunk_markdown

_embedder = EmbeddingClient(
    base_url=settings.llama_embed_url,
    model=settings.llama_embed_model,
    api_key=settings.llama_embed_api_key or None,
)

_UNREACHABLE_ERRORS = (httpx.TransportError,)
_UNREACHABLE_RETRY_CAP_SECONDS = 300


@celery_app.task(name="app.tasks.index.index_file", bind=True, max_retries=3, default_retry_delay=30)
def index_file(self, file_id: int) -> None:
    session = SessionLocal()

    # The file was deleted while this task was queued (an upload cleared by
    # a new chat): nothing to index.
    if session.get(FileRecord, file_id) is None:
        session.close()
        return

    # Already indexed (a duplicate queued while the first run was waiting):
    # nothing to do. A file whose content changed has its old chunks removed
    # by the scan (pipeline_runner._reprocess_changed), so it passes this.
    if session.execute(select(ChunkRecord.id).where(ChunkRecord.file_id == file_id).limit(1)).first():
        session.close()
        return

    job = JobRecord(
        file_id=file_id,
        job_type="index",
        state="running",
        queue=(getattr(self.request, "delivery_info", None) or {}).get("routing_key"),
    )
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
        relative = Path(file_record.path).relative_to(raw_root)
        markdown = (converted_root / relative).with_suffix(".md").read_text(encoding="utf-8")

        chunks = chunk_markdown(markdown)
        rows: list[ChunkRecord] = []
        batch_size = max(1, settings.embed_batch_size)
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            vectors = _embedder.embed(
                [
                    build_embedding_input(file_record.path, file_record.title, file_record.author, c.heading, c.text)
                    for c in batch
                ]
            )
            rows.extend(
                ChunkRecord(
                    file_id=file_id, heading=c.heading, chunk_index=c.index, text=c.text, embedding=vec
                )
                for c, vec in zip(batch, vectors)
            )

        # Replace, don't append: re-indexing after a re-convert must not
        # leave the old version's chunks behind. Embedding finished before
        # this point, so a failure above leaves the old chunks untouched.
        #
        # Indexing is queued both right after conversion and by the
        # auto-ingest timer, so two runs for one file can overlap. The
        # advisory lock (held until commit) makes the delete+insert of one
        # run atomic with respect to the other: the second waits, then
        # replaces the first's chunks instead of adding duplicates beside
        # them. Taken after embedding so it's held only for the DB write.
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": file_id})
        session.execute(delete(ChunkRecord).where(ChunkRecord.file_id == file_id))
        session.add_all(rows)
        job.state = "done"
        session.commit()
    except _UNREACHABLE_ERRORS as exc:
        session.rollback()
        job.error = str(exc)
        job.retries = (job.retries or 0) + 1
        session.commit()
        delay = min(30 * (2**self.request.retries), _UNREACHABLE_RETRY_CAP_SECONDS)
        raise self.retry(exc=exc, countdown=delay, max_retries=None)
    except Exception as exc:
        session.rollback()
        job.state = "failed"
        job.error = str(exc)
        job.retries = (job.retries or 0) + 1
        session.commit()
        raise self.retry(exc=exc)
    finally:
        session.close()

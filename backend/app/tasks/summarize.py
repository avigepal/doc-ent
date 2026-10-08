"""Phase 3 — summarization.

summarize_file is implemented: chunk the converted Markdown, map-summarize
each chunk via llama-server, hierarchically reduce into one file summary,
write to summaries/. Needs a live llama-server at settings.llama_text_url
to actually run (see README's llama.cpp smoke-test step) — the
chunking/map/reduce logic itself is fully unit-tested without one.

If the LLM endpoint is unreachable (DNS failure, connection refused,
timeout), that's treated as "not ready yet" rather than a real failure:
the job stays in "running" state and retries with exponential backoff
instead of being marked "failed" after 3 attempts. This avoids flagging a
folder's status dot red just because the model hasn't been started yet —
it recovers on its own once the endpoint comes back up. Any other
exception (bad markdown, malformed LLM response, etc.) still fails fast
after a few retries, same as before.

reduce (group-level: file -> group -> final) is still a stub — it depends
on the grouping strategy decision (folder vs. sender/thread vs. date
range) from the plan's open decisions.
"""

from pathlib import Path

import httpx

from app.celery_app import celery_app
from app.config import settings
from app.db import SessionLocal
from app.models import FileRecord, JobRecord
from app.summarization.chunker import chunk_markdown
from app.summarization.llm_client import LlamaClient
from app.summarization.map_reduce import map_summarize, reduce_summaries

_llm = LlamaClient(
    base_url=settings.llama_text_url,
    model=settings.llama_text_model,
    api_key=settings.llama_text_api_key or None,
)

# httpx.TransportError covers connection-level failures (DNS, refused,
# timeout) -- i.e. "endpoint unreachable", as opposed to an HTTP error
# response from a reachable server (HTTPStatusError) or an application bug.
_UNREACHABLE_ERRORS = (httpx.TransportError,)
_UNREACHABLE_RETRY_CAP_SECONDS = 300


@celery_app.task(name="app.tasks.summarize.summarize_file", bind=True, max_retries=3, default_retry_delay=30)
def summarize_file(self, file_id: int) -> None:
    session = SessionLocal()

    # The auto-ingest timer (every minute) queues every "converted" file,
    # and a summary takes minutes, so the same file gets queued again and
    # again while the first run is still going. Those duplicates would run
    # one after another and re-summarize an already summarized file. By the
    # time one starts, the first run has finished: skip it.
    existing = session.get(FileRecord, file_id)
    if existing is None or existing.status == "summarized":
        session.close()
        return

    job = JobRecord(
        file_id=file_id,
        job_type="summarize",
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
        summaries_root = Path(settings.data_dir) / "summaries"

        relative = Path(file_record.path).relative_to(raw_root)
        converted_path = (converted_root / relative).with_suffix(".md")
        markdown = converted_path.read_text(encoding="utf-8")

        chunks = chunk_markdown(markdown)
        chunk_summaries = map_summarize(chunks, _llm)
        final_summary = reduce_summaries(chunk_summaries, _llm)

        summary_path = (summaries_root / relative).with_suffix(".md")
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(final_summary, encoding="utf-8")

        file_record.status = "summarized"
        job.state = "done"
        session.commit()
    except _UNREACHABLE_ERRORS as exc:
        job.error = str(exc)
        job.retries = (job.retries or 0) + 1
        session.commit()
        delay = min(30 * (2**self.request.retries), _UNREACHABLE_RETRY_CAP_SECONDS)
        raise self.retry(exc=exc, countdown=delay, max_retries=None)
    except Exception as exc:
        job.state = "failed"
        job.error = str(exc)
        job.retries = (job.retries or 0) + 1
        session.commit()
        raise self.retry(exc=exc)
    finally:
        session.close()


@celery_app.task(name="app.tasks.summarize.reduce")
def reduce(group_key: str) -> None:
    raise NotImplementedError(
        "phase 3: grouping strategy not yet decided (folder vs. sender/thread vs. date range)"
    )

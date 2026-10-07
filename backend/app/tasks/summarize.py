"""Phase 3 — summarization.

summarize_file is implemented: chunk the converted Markdown, map-summarize
each chunk via llama-server, hierarchically reduce into one file summary,
write to summaries/. Needs a live llama-server at settings.llama_text_url
to actually run (see README's llama.cpp smoke-test step) — the
chunking/map/reduce logic itself is fully unit-tested without one.

reduce (group-level: file -> group -> final) is still a stub — it depends
on the grouping strategy decision (folder vs. sender/thread vs. date
range) from the plan's open decisions.
"""

from pathlib import Path

from app.celery_app import celery_app
from app.config import settings
from app.db import SessionLocal
from app.models import FileRecord, JobRecord
from app.summarization.chunker import chunk_markdown
from app.summarization.llm_client import LlamaClient
from app.summarization.map_reduce import map_summarize, reduce_summaries

_llm = LlamaClient(base_url=settings.llama_text_url)


@celery_app.task(name="app.tasks.summarize.summarize_file", bind=True, max_retries=3, default_retry_delay=30)
def summarize_file(self, file_id: int) -> None:
    session = SessionLocal()
    job = JobRecord(file_id=file_id, job_type="summarize", state="running")
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

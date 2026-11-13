"""Phase 4 — search + correlation.

query: the unified entry point the dashboard calls — one retrieval,
reused for a grounded answer AND cross-document/statistical correlation
(see app/search/query.py). ask/correlate below do the same retrieval
individually and stay for direct API use, but the dashboard only calls
`query` now (no separate Ask/Correlate pages).

All three need a live llama-server (text + embeddings) and populated
pgvector embeddings to actually run — the retrieval/prompt/routing logic
itself is fully unit-tested with fakes (tests/test_ask.py,
tests/test_correlate.py, tests/test_query.py).

Statistical mode's `tables` argument (pandas-aggregated spreadsheet data)
isn't wired to a source yet — that depends on where spreadsheet
pre-aggregation gets persisted, which hasn't been built. Pass {} until
then; cross-document + grounded ask work independently of it.

`folders`: when non-empty, scopes retrieval to those top-level folders
under raw/ instead of the whole corpus (see app/search/folder_filter.py).
`author`/`title`: optional substring filters on the document-intrinsic
metadata extracted at convert time (see app/tasks/convert.py:
_apply_document_metadata) — e.g. "files written by this author".
"""

from pathlib import Path

from app.celery_app import celery_app
from app.config import settings
from app.db import SessionLocal
from app.search.ask import answer_grounded
from app.search.correlate import (
    correlate as run_correlate,
    cross_doc_to_dict,
    best_passages_per_file,
    statistical_to_dict,
)
from app.search.numeric_tables import load_numeric_tables
from app.search.embedding_client import EmbeddingClient
from app.search.pgvector_retrieval import retrieve_top_k
from app.search.query import run_chat, run_query
from app.summarization.llm_client import LlamaClient

_embedder = EmbeddingClient(
    base_url=settings.llama_embed_url,
    model=settings.llama_embed_model,
    api_key=settings.llama_embed_api_key or None,
)
_text_llm = LlamaClient(
    base_url=settings.llama_text_url,
    model=settings.llama_text_model,
    api_key=settings.llama_text_api_key or None,
)
_raw_dir = str(Path(settings.data_dir) / "raw")


def _correlation_report_to_dict(report) -> dict:
    return {
        "cross_doc": cross_doc_to_dict(report.cross_doc),
        "statistical": statistical_to_dict(report.statistical),
    }


@celery_app.task(name="app.tasks.correlate.query")
def query(
    question: str,
    k: int = settings.search_top_k,
    folders: list[str] | None = None,
    author: str | None = None,
    title: str | None = None,
    chat_only: bool = False,
    file_ids: list[int] | None = None,
) -> dict:
    """chat_only=True skips retrieval entirely (the dashboard's Scope bar
    with nothing selected — "talk to the model directly") -- see
    app/search/query.py:run_chat. Otherwise this is the normal grounded +
    correlation path: folders=None/[] searches the whole corpus, a
    non-empty list scopes retrieval to those top-level raw/ folders."""
    if chat_only:
        result = run_chat(question, _text_llm)
        return {
            "question": result.question,
            "answer": result.answer,
            "sources": result.sources,
            "grounded": result.grounded,
            **_correlation_report_to_dict(result),
        }

    session = SessionLocal()
    try:
        [query_embedding] = _embedder.embed([question])
        chunks = retrieve_top_k(
            session,
            query_embedding,
            k=k,
            raw_dir=_raw_dir,
            folders=folders,
            author=author,
            title=title,
            query_text=question,
            file_ids=file_ids,
        )
        tables = load_numeric_tables(session, _raw_dir, folders, file_ids)
        result = run_query(question, chunks, tables, _text_llm)
        return {
            "question": result.question,
            "answer": result.answer,
            "sources": result.sources,
            "grounded": result.grounded,
            **_correlation_report_to_dict(result),
        }
    finally:
        session.close()


@celery_app.task(name="app.tasks.correlate.ask")
def ask(
    question: str,
    k: int = settings.search_top_k,
    folders: list[str] | None = None,
    author: str | None = None,
    title: str | None = None,
) -> dict:
    session = SessionLocal()
    try:
        [query_embedding] = _embedder.embed([question])
        chunks = retrieve_top_k(
            session,
            query_embedding,
            k=k,
            raw_dir=_raw_dir,
            folders=folders,
            author=author,
            title=title,
            query_text=question,
        )
        result = answer_grounded(question, chunks, _text_llm)
        return {"answer": result.answer, "sources": result.sources, "grounded": result.grounded}
    finally:
        session.close()


@celery_app.task(name="app.tasks.correlate.correlate")
def correlate(
    question: str,
    k: int = settings.correlate_candidates,
    folders: list[str] | None = None,
    author: str | None = None,
    title: str | None = None,
    file_ids: list[int] | None = None,
) -> dict:
    """Correlation over the whole scope: retrieve widely, keep one passage per document (so many documents
    get a voice), and run statistics over every spreadsheet in scope."""
    session = SessionLocal()
    try:
        [query_embedding] = _embedder.embed([question])
        found = retrieve_top_k(
            session,
            query_embedding,
            k=k,
            raw_dir=_raw_dir,
            folders=folders,
            author=author,
            title=title,
            query_text=question,
            file_ids=file_ids,
        )
        chunks = best_passages_per_file(found, settings.correlate_max_files)
        tables = load_numeric_tables(session, _raw_dir, folders, file_ids)
        report = run_correlate(question, chunks, tables, _text_llm)
        return {"question": report.question, **_correlation_report_to_dict(report)}
    finally:
        session.close()


@celery_app.task(name="app.tasks.correlate.run_view")
def run_view(view_id: int) -> dict:
    """Run a saved view and keep the result on the view (shown on the Dashboards page)."""
    from datetime import datetime, timezone

    from app.models import SavedViewRecord

    session = SessionLocal()
    try:
        row = session.get(SavedViewRecord, view_id)
        if row is None:
            return {"view_id": view_id, "error": "view not found"}
        question, folders = row.question, list(row.folders or [])
    finally:
        session.close()

    result = correlate(question, k=settings.correlate_candidates, folders=folders)

    session = SessionLocal()
    try:
        row = session.get(SavedViewRecord, view_id)
        if row is not None:
            row.last_result = {"question": question, **result}
            row.last_run_at = datetime.now(timezone.utc)
            session.commit()
    finally:
        session.close()
    return {"view_id": view_id, **result}


@celery_app.task(name="app.tasks.correlate.run_due_views")
def run_due_views() -> list[int]:
    """Beat calls this every hour: queue a run for each saved view whose interval has passed."""
    from app.views_store import due_view_ids

    session = SessionLocal()
    try:
        due = due_view_ids(session)
    finally:
        session.close()
    for view_id in due:
        run_view.delay(view_id)
    return due

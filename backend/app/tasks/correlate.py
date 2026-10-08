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
from app.search.correlate import correlate as run_correlate
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
        "cross_doc": None if report.cross_doc is None else {
            "answer": report.cross_doc.answer,
            "sources": report.cross_doc.sources,
        },
        "statistical": None if report.statistical is None else {
            "answer": report.statistical.answer,
            "correlation_summary": report.statistical.correlation_summary,
        },
    }


@celery_app.task(name="app.tasks.correlate.query")
def query(
    question: str,
    k: int = 8,
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
        # TODO: populate from persisted spreadsheet pre-aggregation once
        # that storage exists; statistical mode is a no-op until then.
        tables: dict = {}
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
    k: int = 8,
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
    k: int = 8,
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
        tables: dict = {}
        report = run_correlate(question, chunks, tables, _text_llm)
        return {"question": report.question, **_correlation_report_to_dict(report)}
    finally:
        session.close()

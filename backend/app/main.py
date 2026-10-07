from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine, get_session
from app.pipeline_runner import run_convert_enqueue, run_scan, run_summarize_enqueue


class QuestionRequest(BaseModel):
    question: str
    k: int = 8
    folders: list[str] = []  # top-level folder names under raw/; empty = whole corpus
    author: str | None = None  # case-insensitive substring match, e.g. "files by this author"
    title: str | None = None  # case-insensitive substring match

app = FastAPI(title="Doc Summarization Pipeline API")


@app.on_event("startup")
def _run_startup_migrations() -> None:
    """Applies schema additions to an already-running Postgres (init.sql
    only runs once, on a brand-new volume) — see app/migrations.py."""
    from app.migrations import run_migrations

    run_migrations(engine)


def require_bearer_token(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {settings.bearer_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/ingest/scan", dependencies=[Depends(require_bearer_token)])
def ingest_scan(session: Session = Depends(get_session)) -> dict:
    """Phase 1: walk DATA_DIR/raw, hash + classify every file, upsert into
    Postgres, and return a pre-run summary. Does not touch the GPU — safe
    to run repeatedly (upsert on path). Same logic the auto-ingest beat
    task runs on a timer — see app/pipeline_runner.py."""
    return run_scan(session)


@app.post("/ingest/convert", dependencies=[Depends(require_bearer_token)])
def ingest_convert(session: Session = Depends(get_session)) -> dict:
    """Phase 2: enqueue the implemented conversion tasks (convert_fast,
    convert_email_archive) for every discovered file routed to them.
    convert_ocr/convert_vision aren't wired up yet, so files on those
    queues are left as 'discovered'."""
    return {"enqueued": run_convert_enqueue(session)}


@app.post("/ingest/summarize", dependencies=[Depends(require_bearer_token)])
def ingest_summarize(session: Session = Depends(get_session)) -> dict:
    """Phase 3: enqueue summarize_file for every converted file that
    hasn't been summarized yet. Needs a live llama-server (settings.
    llama_text_url) to actually complete — enqueuing itself has no
    GPU/network dependency."""
    return {"enqueued": {"summarize": run_summarize_enqueue(session)}}


@app.get("/folders", dependencies=[Depends(require_bearer_token)])
def folders_endpoint(session: Session = Depends(get_session)) -> dict:
    """Top-level folders under raw/ with per-folder ingestion status, for
    the dashboard's Ask-page sidebar. Folder names come from the
    filesystem (works even before a scan has run, e.g. a folder just
    created); status counts come from the DB and default to all-zero
    for a folder nothing has been scanned into yet."""
    from app.ingestion.folder_status_query import get_folder_statuses
    from app.ingestion.folders import list_top_level_folders

    raw_dir = Path(settings.data_dir) / "raw"
    disk_folders = list_top_level_folders(raw_dir)
    statuses_by_name = {s.name: s for s in get_folder_statuses(session, str(raw_dir))}

    folders = []
    for name in disk_folders:
        s = statuses_by_name.get(name)
        folders.append({
            "name": name,
            "total_files": s.total_files if s else 0,
            "discovered": s.discovered if s else 0,
            "converted": s.converted if s else 0,
            "summarized": s.summarized if s else 0,
            "processing": s.processing if s else False,
            "has_failures": s.has_failures if s else False,
        })

    return {"folders": folders}


@app.get("/folders/{name}/files", dependencies=[Depends(require_bearer_token)])
def folder_files_endpoint(name: str, session: Session = Depends(get_session)) -> dict:
    """Per-file metadata (status, mime, size, plus the document-intrinsic
    title/author/page_count/doc_created_at pulled at convert time — see
    app/tasks/convert.py:_apply_document_metadata) for everything under
    one folder. Fields are null until that file's been converted, or if
    the backend that converted it couldn't determine them."""
    from sqlalchemy import or_, select

    from app.models import FileRecord
    from app.search.folder_filter import build_folder_like_patterns

    raw_dir = str(Path(settings.data_dir) / "raw")
    patterns = build_folder_like_patterns(raw_dir, [name])
    if not patterns:
        return {"files": []}

    rows = session.execute(
        select(FileRecord).where(or_(*(FileRecord.path.like(p) for p in patterns)))
    ).scalars().all()

    return {
        "files": [
            {
                "path": r.path,
                "status": r.status,
                "mime_type": r.mime_type,
                "size_bytes": r.size_bytes,
                "title": r.title,
                "author": r.author,
                "page_count": r.page_count,
                "doc_created_at": r.doc_created_at.isoformat() if r.doc_created_at else None,
                "discovered_at": r.discovered_at.isoformat(),
            }
            for r in rows
        ]
    }


class CreateFolderRequest(BaseModel):
    name: str


@app.post("/folders", dependencies=[Depends(require_bearer_token)])
def create_folder_endpoint(payload: CreateFolderRequest) -> dict:
    """Lets the dashboard create a new top-level folder under raw/ directly
    — same sanitization as export filenames (app/export/adhoc.py) so a
    folder name can never escape raw/ via '..' or path separators. Once
    created, drop files into it via SSH/SFTP/FileZilla straight into
    HOST_DATA_DIR/raw/<name> on the server, or the dashboard's "+" upload;
    auto-ingest (or a manual Scan) picks it up like any other folder."""
    from app.export.adhoc import safe_filename

    raw_dir = Path(settings.data_dir) / "raw"
    folder_name = safe_filename(payload.name)
    (raw_dir / folder_name).mkdir(parents=True, exist_ok=True)
    return {"created": folder_name}


@app.post("/query", dependencies=[Depends(require_bearer_token)])
def query_endpoint(payload: QuestionRequest, session: Session = Depends(get_session)) -> dict:
    """Phase 4: the single unified entry point the dashboard calls — one
    retrieval, reused for a grounded answer AND cross-document/statistical
    correlation (whichever apply). Runs synchronously (request/response).
    Needs a live llama-server (text + embeddings) and populated pgvector
    embeddings. See app/tasks/correlate.py:query.

    The result is recorded to query_history server-side (not in the
    browser) so it survives the tab closing, and `history_id` comes back
    so a follow-up /export can link to this query."""
    from app.history.store import record_query
    from app.tasks.correlate import query

    result = query(
        payload.question,
        k=payload.k,
        folders=payload.folders,
        author=payload.author,
        title=payload.title,
    )
    history_id = record_query(
        session,
        question=payload.question,
        result=result,
        folders=payload.folders,
        author=payload.author,
        title=payload.title,
    )
    return {**result, "history_id": history_id}


MAX_UPLOAD_FILES = 50
MAX_UPLOAD_TOTAL_BYTES = 100 * 1024 * 1024  # 100MB


@app.post("/query/upload", dependencies=[Depends(require_bearer_token)])
async def query_upload_endpoint(
    question: str = Form(...),
    files: list[UploadFile] = File(...),
    author: str | None = Form(None),  # case-insensitive substring match, same as /query's author
    title: str | None = Form(None),  # case-insensitive substring match, same as /query's title
    session: Session = Depends(get_session),
) -> dict:
    """The dashboard's ChatGPT/OpenWebUI-style "+" attach button: convert
    the uploaded file(s) on the spot and answer strictly from them —
    these files are never written into DATA_DIR/raw or the corpus, they
    exist only for this one question. Needs a live llama-server (text) —
    unlike /query, this doesn't need embeddings/pgvector at all, since
    there's no retrieval: every uploaded file is included directly.

    author/title give this the same filtering /query has: each attached
    file's own extracted metadata is checked against the filter, and a
    non-matching file is excluded from the answer — this is "which of my
    attached files" rather than "which of the corpus", but the filter
    semantics (case-insensitive substring) are identical either way. See
    app/search/adhoc_upload.py."""
    from app.search.adhoc_upload import convert_upload_to_chunks
    from app.search.query import run_query
    from app.tasks.correlate import _correlation_report_to_dict, _text_llm

    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail=f"too many files (max {MAX_UPLOAD_FILES})")

    all_chunks = []
    total_bytes = 0
    for f in files:
        content = await f.read()
        total_bytes += len(content)
        if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"uploads exceed {MAX_UPLOAD_TOTAL_BYTES // (1024 * 1024)}MB total",
            )
        all_chunks.extend(
            convert_upload_to_chunks(
                f.filename or "upload", content, f.content_type, author_filter=author, title_filter=title
            )
        )

    if not all_chunks and (author or title):
        raise HTTPException(
            status_code=400,
            detail="no attached file matched the given author/title filter",
        )

    # similarity_threshold=0.0: uploaded chunks are always scored 1.0 (explicitly
    # provided by the user) and should never be filtered out as "not found".
    result = run_query(question, all_chunks, tables={}, llm=_text_llm, similarity_threshold=0.0)

    from app.history.store import record_query

    payload = {
        "question": result.question,
        "answer": result.answer,
        "sources": result.sources,
        "grounded": result.grounded,
        **_correlation_report_to_dict(result),
    }
    history_id = record_query(
        session,
        question=question,
        result=payload,
        author=author,
        title=title,
        attached_filenames=[f.filename or "upload" for f in files],
    )
    return {**payload, "history_id": history_id}


@app.post("/ask", dependencies=[Depends(require_bearer_token)])
def ask_endpoint(payload: QuestionRequest) -> dict:
    """Phase 4: grounded Ask only, no correlation. Kept for direct API use
    — the dashboard calls /query instead. See app/tasks/correlate.py:ask."""
    from app.tasks.correlate import ask

    return ask(payload.question, k=payload.k, folders=payload.folders, author=payload.author, title=payload.title)


@app.post("/correlate", dependencies=[Depends(require_bearer_token)])
def correlate_endpoint(payload: QuestionRequest) -> dict:
    """Phase 4: correlation only, no grounded answer. Kept for direct API
    use — the dashboard calls /query instead. See
    app/tasks/correlate.py:correlate."""
    from app.tasks.correlate import correlate

    return correlate(payload.question, k=payload.k, folders=payload.folders, author=payload.author, title=payload.title)


class ExportRequest(BaseModel):
    # Either relative_path (export an existing file under summaries/) or
    # content (export arbitrary text on the spot, e.g. a query result the
    # dashboard built client-side) — exactly one of the two.
    relative_path: str | None = None
    content: str | None = None
    filename: str = "export"
    fmt: str = "pdf"
    # Set by the dashboard from the /query response so the export shows up
    # on the History page linked to the query that produced it.
    history_id: int | None = None


_EXPORT_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "json": "application/json",
}


@app.post("/export", dependencies=[Depends(require_bearer_token)])
def export_endpoint(payload: ExportRequest, session: Session = Depends(get_session)) -> FileResponse:
    """Phase 5/7: export to PDF (default) / docx / json via pandoc and
    stream the file straight back as a download — no separate download
    step or endpoint. Needs pandoc + a PDF engine (tectonic/xelatex)
    installed — see app/export/pandoc_export.py and app/export/adhoc.py."""
    exports_root = Path(settings.data_dir) / "exports"

    if payload.content is not None:
        from app.export.adhoc import prepare_adhoc_export

        output_path = prepare_adhoc_export(payload.content, payload.filename, exports_root, fmt=payload.fmt)
    elif payload.relative_path is not None:
        from app.export.pandoc_export import export_markdown

        summaries_root = Path(settings.data_dir) / "summaries"
        source = summaries_root / payload.relative_path
        if not source.exists():
            raise HTTPException(status_code=404, detail=f"no summary at {payload.relative_path}")
        output_path = export_markdown(source, exports_root / Path(payload.relative_path).parent, fmt=payload.fmt)
    else:
        raise HTTPException(status_code=400, detail="either content or relative_path is required")

    from app.history.store import record_export

    record_export(
        session,
        query_history_id=payload.history_id,
        filename=payload.filename,
        fmt=payload.fmt,
        stored_path=output_path,
    )

    return FileResponse(
        output_path,
        media_type=_EXPORT_MEDIA_TYPES.get(payload.fmt, "application/octet-stream"),
        filename=output_path.name,
    )


@app.get("/history/queries", dependencies=[Depends(require_bearer_token)])
def history_queries_endpoint(
    limit: int = 50, offset: int = 0, session: Session = Depends(get_session)
) -> dict:
    """Paged query history, newest first. Summary shape — call
    /history/queries/{id} for the stored answer."""
    from app.history.serialization import query_record_to_summary
    from app.history.store import list_queries

    records = list_queries(session, limit=min(limit, 200), offset=offset)
    return {"queries": [query_record_to_summary(r) for r in records]}


@app.get("/history/queries/{history_id}", dependencies=[Depends(require_bearer_token)])
def history_query_detail_endpoint(
    history_id: int, session: Session = Depends(get_session)
) -> dict:
    """Full stored snapshot, so the History page can re-render a past
    result without re-running the model."""
    from app.history.serialization import query_record_to_detail
    from app.history.store import get_query

    record = get_query(session, history_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such history entry")
    return query_record_to_detail(record)


@app.delete("/history/queries/{history_id}", dependencies=[Depends(require_bearer_token)])
def history_query_delete_endpoint(
    history_id: int, session: Session = Depends(get_session)
) -> dict:
    from app.history.store import delete_query

    if not delete_query(session, history_id):
        raise HTTPException(status_code=404, detail="no such history entry")
    return {"deleted": history_id}


@app.get("/history/exports", dependencies=[Depends(require_bearer_token)])
def history_exports_endpoint(
    limit: int = 50, offset: int = 0, session: Session = Depends(get_session)
) -> dict:
    from app.history.serialization import export_record_to_dict
    from app.history.store import list_exports

    records = list_exports(session, limit=min(limit, 200), offset=offset)
    return {"exports": [export_record_to_dict(r) for r in records]}


@app.get("/history/exports/{export_id}/download", dependencies=[Depends(require_bearer_token)])
def history_export_download_endpoint(
    export_id: int, session: Session = Depends(get_session)
) -> FileResponse:
    """Re-streams a previously generated export from disk. Exports live
    under DATA_DIR/exports, so this is a real re-download rather than a
    re-render — which is why history is in Postgres and not localStorage."""
    from app.history.store import get_export

    record = get_export(session, export_id)
    if record is None:
        raise HTTPException(status_code=404, detail="no such export")

    path = Path(record.stored_path)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="exported file is no longer on disk — re-run the export to regenerate it",
        )

    return FileResponse(
        path,
        media_type=_EXPORT_MEDIA_TYPES.get(record.fmt, "application/octet-stream"),
        filename=path.name,
    )


@app.get("/stats", dependencies=[Depends(require_bearer_token)])
def stats_endpoint(session: Session = Depends(get_session)) -> dict:
    """Overview metrics for the dashboard home page."""
    from app.stats_query import get_overview_stats

    return get_overview_stats(session, Path(settings.data_dir) / "raw")


@app.get("/documents", dependencies=[Depends(require_bearer_token)])
def documents_endpoint(
    folder: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> dict:
    """Every ingested file with its metadata, in one call. Backs the
    Documents page's table."""
    from app.ingestion.documents_query import list_documents

    documents, total = list_documents(
        session,
        raw_dir=str(Path(settings.data_dir) / "raw"),
        folder=folder,
        status=status,
        search=q,
        limit=min(limit, 500),
        offset=offset,
    )
    return {"documents": documents, "total": total}


# Phase 7: serve the built React dashboard as static files so the whole
# app is one process, one port. Registered last so it doesn't shadow the
# API routes above (FastAPI matches routes in registration order). In
# the Docker image, DASHBOARD_DIST_DIR points at the dashboard-build
# stage's output (see backend/Dockerfile); for local `uvicorn` dev it
# falls back to dashboard/dist relative to this repo, which is fine to
# be missing (before `npm run build` has run once) — the dashboard is
# served by Vite's own dev server (localhost:5173) then.
#
# A plain StaticFiles mount at "/" would swallow every path itself and
# 404 on anything that isn't a real file on disk — breaking a refresh or
# direct link to a client-side route like /ask. This catch-all serves
# the exact file when one exists (JS/CSS/icons under assets/, favicon,
# etc.) and falls back to index.html otherwise, letting React Router's
# BrowserRouter handle the rest client-side.
_dashboard_dist = (
    Path(settings.dashboard_dist_dir)
    if settings.dashboard_dist_dir
    else Path(__file__).resolve().parent.parent.parent / "dashboard" / "dist"
)
if _dashboard_dist.exists():

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_dashboard(full_path: str) -> FileResponse:
        candidate = _dashboard_dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_dashboard_dist / "index.html")

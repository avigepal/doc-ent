import json
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine, get_session
from app.queue_guard import UPLOAD_QUEUE
from app.pipeline_runner import (
    run_convert_enqueue,
    register_files,
    run_index_enqueue,
    run_retry_failed,
    run_scan,
    run_summarize_enqueue,
)


class QuestionRequest(BaseModel):
    question: str
    k: int = 8
    folders: list[str] = []  # top-level folder names under raw/; empty = whole corpus
    author: str | None = None  # case-insensitive substring match, e.g. "files by this author"
    title: str | None = None  # case-insensitive substring match
    # The dashboard's Scope bar has three states: "All" selected (whole
    # corpus, folders=[]), one or more folders selected (folders=[...]),
    # or nothing selected at all -- chat_only=True, which skips retrieval
    # entirely and talks to the model directly. folders is ignored when
    # chat_only is set.
    chat_only: bool = False
    # Files attached to this chat (the dashboard's upload button). When
    # given, the search covers exactly these files and folders is ignored.
    file_ids: list[int] | None = None
    # Client-generated UUID identifying which chat thread this question
    # belongs to (see dashboard/src/pages/Ask.tsx) -- "" groups it with
    # the other ungrouped legacy rows from before this field existed.
    conversation_id: str = ""

app = FastAPI(title="Docent API")


@app.on_event("startup")
def _run_startup_migrations() -> None:
    """Applies schema additions to an already-running Postgres (init.sql
    only runs once, on a brand-new volume) — see app/migrations.py."""
    from app.migrations import run_migrations

    run_migrations(engine)


@app.on_event("startup")
def _preload_task_modules() -> None:
    """The ingest endpoints import the Celery tasks lazily; the first upload
    after a restart then paid ~3 seconds of one-time imports before
    answering. Loading them at startup moves that cost out of the request."""
    import app.tasks.convert  # noqa: F401
    import app.tasks.index  # noqa: F401
    import app.tasks.summarize  # noqa: F401


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
    convert_email_archive, convert_vision) for every discovered file routed
    to them. convert_ocr isn't wired up yet, so files on that queue are
    marked 'unsupported' by the scan."""
    return {"enqueued": run_convert_enqueue(session)}


@app.post("/ingest/summarize", dependencies=[Depends(require_bearer_token)])
def ingest_summarize(session: Session = Depends(get_session)) -> dict:
    """Phase 3: enqueue summarize_file for every converted file that
    hasn't been summarized yet. Needs a live llama-server (settings.
    llama_text_url) to actually complete — enqueuing itself has no
    GPU/network dependency."""
    return {"enqueued": {"summarize": run_summarize_enqueue(session)}}


@app.post("/ingest/retry-failed", dependencies=[Depends(require_bearer_token)])
def ingest_retry_failed(session: Session = Depends(get_session)) -> dict:
    """Re-queues every file that ended up "failed" (conversion ran out of
    retries). Use after fixing whatever made them fail."""
    reset = run_retry_failed(session)
    return {"reset": reset, "enqueued": run_convert_enqueue(session)}


@app.post("/ingest/index", dependencies=[Depends(require_bearer_token)])
def ingest_index(session: Session = Depends(get_session)) -> dict:
    """Phase 4: chunk + embed every converted file that has no chunks yet,
    so search can find it. Needs the embedding server (settings.
    llama_embed_url) to complete. Also runs automatically each
    auto-ingest cycle."""
    return {"enqueued": {"index": run_index_enqueue(session)}}


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
            "unsupported": s.unsupported if s else 0,
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


def _catalog_answer(session: Session, payload: "QuestionRequest", raw_dir: str) -> dict | None:
    """Questions about the library itself ("list all files", "how many
    documents") are answered from the files table, scoped like a normal
    query (selected folders / author / title). None means "not a library
    question" -- the caller runs the usual search. See app/search/catalog.py."""
    from app.search.catalog import format_catalog_answer, is_catalog_question, list_catalog

    if payload.chat_only or not is_catalog_question(payload.question):
        return None

    entries = list_catalog(session, raw_dir, payload.folders, payload.author, payload.title, payload.file_ids)
    answer, sources = format_catalog_answer(
        entries, payload.folders, payload.author, payload.title, attached=bool(payload.file_ids)
    )
    return {
        "question": payload.question,
        "answer": answer,
        "sources": sources,
        "grounded": True,
        "cross_doc": None,
        "statistical": None,
    }


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
    from app.tasks.correlate import _raw_dir, query

    catalog_result = _catalog_answer(session, payload, _raw_dir)
    result = catalog_result or query(
        payload.question,
        k=payload.k,
        folders=payload.folders,
        author=payload.author,
        title=payload.title,
        chat_only=payload.chat_only,
        file_ids=payload.file_ids,
    )
    history_id = record_query(
        session,
        question=payload.question,
        result=result,
        folders=payload.folders,
        author=payload.author,
        title=payload.title,
        chat_only=payload.chat_only,
        conversation_id=payload.conversation_id,
    )
    return {**result, "history_id": history_id}


@app.post("/query/stream", dependencies=[Depends(require_bearer_token)])
def query_stream_endpoint(payload: QuestionRequest, session: Session = Depends(get_session)) -> StreamingResponse:
    """SSE variant of /query: the main answer streams token-by-token as
    the model generates it, instead of the client waiting for the whole
    response. Event stream shape (see app/search/query.py:stream_query):
    one "meta" (sources + grounded), then "token" events (concatenate
    their text for the full answer), then one "extra" (cross_doc/
    statistical, both null for chat_only/ungrounded), then one "done"
    (history_id) once this endpoint has recorded the completed turn —
    same recording /query does, just after the stream finishes instead
    of before responding."""
    from app.history.store import record_query
    from app.search.catalog import catalog_events
    from app.search.pgvector_retrieval import retrieve_top_k
    from app.search.query import stream_query
    from app.tasks.correlate import _embedder, _raw_dir, _text_llm

    def event_source():
        answer_parts: list[str] = []
        meta = {"sources": [], "grounded": False}
        extra = {"cross_doc": None, "statistical": None}
        try:
            catalog = _catalog_answer(session, payload, _raw_dir)
            if catalog is not None:
                chunks = []
                events = catalog_events(catalog["answer"], catalog["sources"])
            elif payload.chat_only:
                chunks = []
            else:
                [query_embedding] = _embedder.embed([payload.question])
                chunks = retrieve_top_k(
                    session,
                    query_embedding,
                    k=payload.k,
                    raw_dir=_raw_dir,
                    folders=payload.folders,
                    author=payload.author,
                    title=payload.title,
                    query_text=payload.question,
                    file_ids=payload.file_ids,
                )

            if catalog is None:
                events = stream_query(payload.question, chunks, {}, _text_llm, chat_only=payload.chat_only)
            for evt in events:
                if evt["event"] == "meta":
                    meta = evt["data"]
                elif evt["event"] == "token":
                    answer_parts.append(evt["data"]["text"])
                elif evt["event"] == "extra":
                    extra = evt["data"]
                yield f"event: {evt['event']}\ndata: {json.dumps(evt['data'])}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"
            return

        result = {
            "question": payload.question,
            "answer": "".join(answer_parts),
            "sources": meta.get("sources", []),
            "grounded": meta.get("grounded", False),
            **extra,
        }
        history_id = record_query(
            session,
            question=payload.question,
            result=result,
            folders=payload.folders,
            author=payload.author,
            title=payload.title,
            chat_only=payload.chat_only,
            conversation_id=payload.conversation_id,
        )
        yield f"event: done\ndata: {json.dumps({'history_id': history_id})}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


MAX_UPLOAD_FILES = 50
MAX_UPLOAD_TOTAL_BYTES = 100 * 1024 * 1024  # 100MB


@app.post("/ingest/upload", dependencies=[Depends(require_bearer_token)])
def ingest_upload_endpoint(
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
) -> dict:
    """The dashboard's "+" attach button: persists the uploaded file(s)
    into a dedicated raw/uploads/ folder — never into whatever folder is
    selected in Scope, or any other corpus folder — then registers just
    those files and queues their conversion immediately (conversion queues
    indexing itself when it finishes, so they become searchable without
    waiting for the auto-ingest timer). Deliberately NOT a full corpus
    scan, and a plain `def` so FastAPI runs the blocking file/DB work in
    its thread pool instead of stalling every other request.
    See app/ingestion/uploads.py."""
    from app.ingestion.uploads import UPLOAD_FOLDER_NAME, safe_upload_filename, unique_destination

    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail=f"too many files (max {MAX_UPLOAD_FILES})")

    upload_dir = Path(settings.data_dir) / "raw" / UPLOAD_FOLDER_NAME
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_names = []
    saved_paths = []
    total_bytes = 0
    for f in files:
        content = f.file.read()
        total_bytes += len(content)
        if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"uploads exceed {MAX_UPLOAD_TOTAL_BYTES // (1024 * 1024)}MB total",
            )
        dest = unique_destination(upload_dir, safe_upload_filename(f.filename or "upload"))
        dest.write_bytes(content)
        saved_names.append(dest.name)
        saved_paths.append(dest)

    file_ids = register_files(session, saved_paths)
    # the dedicated upload lane: its own worker converts AND indexes these,
    # so they don't wait behind whatever bulk ingest has queued
    convert_enqueued = run_convert_enqueue(session, file_ids, queue=UPLOAD_QUEUE)

    return {
        "folder": UPLOAD_FOLDER_NAME,
        "uploaded": saved_names,
        "file_ids": file_ids,
        "registered": len(file_ids),
        "convert_enqueued": convert_enqueued,
    }


@app.delete("/ingest/uploads", dependencies=[Depends(require_bearer_token)])
def clear_uploads_endpoint(session: Session = Depends(get_session)) -> dict:
    """Empties the uploads folder -- the dashboard calls this when a new chat
    starts, since uploads are that chat's attachments. Removes the files, their
    converted text, summaries, chunks and job records. Only ever touches the
    dedicated uploads folder, never a corpus folder. See uploads.py."""
    from app.ingestion.uploads import delete_uploads

    return {"deleted": delete_uploads(session, settings.data_dir)}


@app.delete("/ingest/uploads/{file_id}", dependencies=[Depends(require_bearer_token)])
def delete_upload_endpoint(file_id: int, session: Session = Depends(get_session)) -> dict:
    """Removes one attached file (the chip's X). Only files in the uploads
    folder can be deleted this way."""
    from app.ingestion.uploads import delete_uploads

    return {"deleted": delete_uploads(session, settings.data_dir, [file_id])}


@app.get("/ingest/upload/status", dependencies=[Depends(require_bearer_token)])
def ingest_upload_status(ids: str, session: Session = Depends(get_session)) -> dict:
    """Where freshly uploaded files are (queued / converting / indexing /
    ready / failed / unsupported), for the Ask page's upload notice. `ids`
    is the comma-separated file_ids the upload response returned. See
    app/ingestion/upload_status.py."""
    from app.ingestion.upload_status import TERMINAL_STAGES, get_upload_status

    try:
        file_ids = [int(part) for part in ids.split(",") if part.strip()][:MAX_UPLOAD_FILES]
    except ValueError:
        raise HTTPException(status_code=400, detail="ids must be comma-separated integers")

    files = get_upload_status(session, file_ids)
    return {"files": files, "done": all(f["stage"] in TERMINAL_STAGES for f in files)}


@app.post("/query/upload", dependencies=[Depends(require_bearer_token)])
async def query_upload_endpoint(
    question: str = Form(...),
    files: list[UploadFile] = File(...),
    author: str | None = Form(None),  # case-insensitive substring match, same as /query's author
    title: str | None = Form(None),  # case-insensitive substring match, same as /query's title
    session: Session = Depends(get_session),
) -> dict:
    """Kept for direct API use (the dashboard's "+" attach button now
    calls /ingest/upload instead, which persists into raw/uploads/ — see
    app/ingestion/uploads.py): convert the uploaded file(s) on the spot
    and answer strictly from them — these files are never written into
    DATA_DIR/raw or the corpus, they exist only for this one question.
    Needs a live llama-server (text) — unlike /query, this doesn't need
    embeddings/pgvector at all, since there's no retrieval: every
    uploaded file is included directly.

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
    limit: int = 50,
    offset: int = 0,
    detail: bool = False,
    conversation_id: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    """Paged query history, newest first. Summary shape by default — call
    /history/queries/{id} for one stored answer, or pass detail=true here
    to get every answer body in one call. Pass conversation_id to scope
    this to one chat thread (used by the Ask page to restore that
    specific thread on reload without an N+1 fetch); omit it for the
    History page's cross-conversation list."""
    from app.history.serialization import query_record_to_detail, query_record_to_summary
    from app.history.store import list_queries

    records = list_queries(session, limit=min(limit, 200), offset=offset, conversation_id=conversation_id)
    shape = query_record_to_detail if detail else query_record_to_summary
    return {"queries": [shape(r) for r in records]}


@app.get("/history/conversations", dependencies=[Depends(require_bearer_token)])
def history_conversations_endpoint(limit: int = 50, session: Session = Depends(get_session)) -> dict:
    """The Ask page's sidebar chat list: one row per conversation_id,
    titled by its opening question, newest-active first. See
    app/history/store.py:list_conversations."""
    from app.history.store import list_conversations

    rows = list_conversations(session, limit=min(limit, 200))
    return {
        "conversations": [
            {
                "conversation_id": r["conversation_id"],
                "title": r["title"],
                "last_at": r["last_at"].isoformat() if r["last_at"] else None,
                "message_count": r["message_count"],
                "pinned": r["pinned"],
            }
            for r in rows
        ]
    }


class PinRequest(BaseModel):
    pinned: bool


@app.put("/history/conversations/{conversation_id}/pin", dependencies=[Depends(require_bearer_token)])
def history_conversation_pin_endpoint(
    conversation_id: str, payload: PinRequest, session: Session = Depends(get_session)
) -> dict:
    from app.history.store import set_conversation_pinned

    set_conversation_pinned(session, conversation_id, payload.pinned)
    return {"conversation_id": conversation_id, "pinned": payload.pinned}


@app.delete("/history/conversations/{conversation_id}", dependencies=[Depends(require_bearer_token)])
def history_conversation_delete_endpoint(conversation_id: str, session: Session = Depends(get_session)) -> dict:
    from app.history.store import delete_conversation

    deleted = delete_conversation(session, conversation_id)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="no such conversation")
    return {"deleted": conversation_id, "messages": deleted}


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


@app.get("/ingestion/progress", dependencies=[Depends(require_bearer_token)])
def ingestion_progress_endpoint(session: Session = Depends(get_session)) -> dict:
    """What the pipeline is doing right now: overall + per-folder counts,
    jobs currently running (with elapsed time), failures with their
    error, and the most recent completions. Backs the Progress page,
    which polls it. See app/ingestion/progress.py."""
    from app.ingestion.progress_query import get_ingestion_progress

    return get_ingestion_progress(session, str(Path(settings.data_dir) / "raw"))


@app.get("/stats", dependencies=[Depends(require_bearer_token)])
def stats_endpoint(session: Session = Depends(get_session)) -> dict:
    """Overview metrics for the dashboard home page."""
    from app.stats_query import get_overview_stats

    return get_overview_stats(session, Path(settings.data_dir) / "raw")


# Not "/documents": that is also the dashboard page's URL, so a refresh or
# bookmark of the page would hit this endpoint and show raw JSON instead of
# the app. Every API path must stay distinct from a dashboard route.
@app.get("/documents/list", dependencies=[Depends(require_bearer_token)])
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

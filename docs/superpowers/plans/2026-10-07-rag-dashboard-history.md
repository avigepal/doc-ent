# RAG Dashboard Restructure + Query/Export History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the two-page dashboard into a five-section RAG console with a persistent sidebar, and add Postgres-backed history for every query run and every file exported.

**Architecture:** Two new Postgres tables (`query_history`, `export_history`) recorded server-side by the existing `/query`, `/query/upload` and `/export` endpoints, exposed through new read endpoints alongside a `/stats` rollup and a corpus-wide `/documents` listing. The React dashboard moves from top-tab navigation to a sidebar shell with Overview / Ask / Documents / History / Ingestion, restyled denser on the existing CSS custom-property palette.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL + pgvector, React 19 + Vite + TypeScript, Tailwind CSS v4, React Router.

**Spec:** `docs/superpowers/specs/2026-10-07-rag-dashboard-history-design.md`

## Global Constraints

- **This project is not under version control.** `git rev-parse` fails in `D:\dev\doc`. The "Commit" step in each task below cannot run as written. Either run `git init` first (recommended — this plan touches ~20 files and there is currently no way to undo a bad edit), or treat each commit step as a checkpoint where you stop and verify before continuing. Do not skip the verification.
- **No Alembic.** Schema changes go in `backend/app/migrations.py` as idempotent statements run at startup, and are mirrored into `backend/sql/init.sql` for fresh installs. Do not introduce a migration framework.
- **The SPA catch-all route in `backend/app/main.py` must stay registered last.** FastAPI matches routes in registration order; any new endpoint added after `@app.get("/{full_path:path}")` will be shadowed by it and return the dashboard HTML instead of JSON.
- **Keep every CSS custom property in `dashboard/src/index.css` unchanged.** The denser restyle adjusts sizing and spacing only. Changing the tokens breaks dark mode, which is defined entirely through them.
- **`stored_path` is never returned to clients.** It is an absolute server filesystem path; exposing it leaks server layout. Downloads go through `GET /history/exports/{id}/download`, which resolves the path server-side.
- **Embedding dimension is fixed at 1024** (`ChunkRecord.embedding`). Nothing in this plan changes it.
- **No frontend test runner exists** in `dashboard/`. The frontend test cycle for every UI task is `npx tsc --noEmit` plus explicit browser verification. Adding a frontend test framework is out of scope.
- **Load `dataviz` before Task 10** (KPI tiles and the stacked status bar) and `frontend-design` before Task 7 (the restyle). Both apply directly.

---

### Task 1: History tables — models, migration, init.sql

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/migrations.py`
- Modify: `backend/sql/init.sql`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `QueryHistoryRecord` and `ExportHistoryRecord` SQLAlchemy models importable from `app.models`, with the column names every later backend task uses.

- [ ] **Step 1: Add the two models**

In `backend/app/models.py`, extend the existing sqlalchemy import line to include `Boolean`, add the JSONB import, and append both models at the end of the file:

```python
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
```

```python
class QueryHistoryRecord(Base):
    """One row per query run through /query or /query/upload. Stores the
    full result, not just the question: re-running a query against a local
    LLM is slow, so the History page restores the stored answer instead."""

    __tablename__ = "query_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    grounded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cross_doc: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    statistical: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # filter_* rather than author/title: `files` already has title/author
    # columns meaning document-intrinsic metadata, and an unprefixed name
    # here would be ambiguous in any query joining both tables.
    filter_folders: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    filter_author: Mapped[str | None] = mapped_column(Text, nullable=True)
    filter_title: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Non-empty only for "+"-attach queries, which bypass the corpus.
    attached_filenames: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ExportHistoryRecord(Base):
    __tablename__ = "export_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # SET NULL, not CASCADE: deleting a query shouldn't destroy the record
    # of a file that still exists on disk and is still downloadable.
    query_history_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("query_history.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    fmt: Mapped[str] = mapped_column(String, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [ ] **Step 2: Add the migration statements**

In `backend/app/migrations.py`, append to `_STATEMENTS`:

```python
    """
    CREATE TABLE IF NOT EXISTS query_history (
        id BIGSERIAL PRIMARY KEY,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        sources JSONB NOT NULL DEFAULT '[]'::jsonb,
        grounded BOOLEAN NOT NULL DEFAULT FALSE,
        cross_doc JSONB,
        statistical JSONB,
        filter_folders JSONB NOT NULL DEFAULT '[]'::jsonb,
        filter_author TEXT,
        filter_title TEXT,
        attached_filenames JSONB NOT NULL DEFAULT '[]'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS export_history (
        id BIGSERIAL PRIMARY KEY,
        query_history_id BIGINT REFERENCES query_history(id) ON DELETE SET NULL,
        filename TEXT NOT NULL,
        fmt VARCHAR NOT NULL,
        stored_path TEXT NOT NULL,
        size_bytes BIGINT NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_query_history_created_at ON query_history (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_export_history_created_at ON export_history (created_at DESC)",
```

- [ ] **Step 3: Mirror the DDL into init.sql**

Append the same two `CREATE TABLE IF NOT EXISTS` blocks and two index statements to `backend/sql/init.sql`, so a brand-new Postgres volume gets them without waiting for the startup migration.

- [ ] **Step 4: Verify the models import cleanly**

Run: `cd backend && python -c "from app.models import QueryHistoryRecord, ExportHistoryRecord; print(QueryHistoryRecord.__tablename__, ExportHistoryRecord.__tablename__)"`
Expected: `query_history export_history`

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/app/migrations.py backend/sql/init.sql
git commit -m "feat: add query_history and export_history tables"
```

---

### Task 2: History serialization (pure)

**Files:**
- Create: `backend/app/history/__init__.py`
- Create: `backend/app/history/serialization.py`
- Test: `backend/tests/test_history_serialization.py`

**Interfaces:**
- Consumes: the column names defined in Task 1.
- Produces: `query_record_to_summary(record) -> dict`, `query_record_to_detail(record) -> dict`, `export_record_to_dict(record) -> dict`. Tasks 3 and 4 call these to shape API responses.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_history_serialization.py`:

```python
from datetime import datetime, timezone
from types import SimpleNamespace

from app.history.serialization import (
    export_record_to_dict,
    query_record_to_detail,
    query_record_to_summary,
)

AT = datetime(2026, 10, 7, 12, 30, tzinfo=timezone.utc)


def _query_record(**overrides):
    base = dict(
        id=1,
        question="who signed the lease?",
        answer="Alice signed it [1].",
        sources=["/data/pipeline/raw/contracts/lease.pdf"],
        grounded=True,
        cross_doc=None,
        statistical=None,
        filter_folders=["contracts"],
        filter_author=None,
        filter_title=None,
        attached_filenames=[],
        created_at=AT,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_summary_omits_the_answer_body_so_the_list_stays_light():
    result = query_record_to_summary(_query_record())

    assert "answer" not in result
    assert "sources" not in result
    assert result["source_count"] == 1
    assert result["question"] == "who signed the lease?"
    assert result["created_at"] == AT.isoformat()


def test_detail_includes_the_full_snapshot():
    result = query_record_to_detail(_query_record())

    assert result["answer"] == "Alice signed it [1]."
    assert result["sources"] == ["/data/pipeline/raw/contracts/lease.pdf"]
    assert result["source_count"] == 1


def test_detail_carries_correlation_blocks_when_present():
    record = _query_record(
        cross_doc={"answer": "both mention Q2", "sources": ["a", "b"]},
        statistical={"answer": "strong link", "correlation_summary": "x vs y: r=0.91"},
    )

    result = query_record_to_detail(record)

    assert result["cross_doc"]["answer"] == "both mention Q2"
    assert result["statistical"]["correlation_summary"] == "x vs y: r=0.91"


def test_null_json_columns_become_empty_lists_not_none():
    record = _query_record(sources=None, filter_folders=None, attached_filenames=None)

    result = query_record_to_detail(record)

    assert result["sources"] == []
    assert result["filter_folders"] == []
    assert result["attached_filenames"] == []
    assert result["source_count"] == 0


def test_export_dict_never_exposes_the_server_filesystem_path():
    record = SimpleNamespace(
        id=7,
        query_history_id=1,
        filename="who-signed-the-lease",
        fmt="pdf",
        stored_path="/data/pipeline/exports/adhoc/who-signed-the-lease.pdf",
        size_bytes=20480,
        created_at=AT,
    )

    result = export_record_to_dict(record)

    assert "stored_path" not in result
    assert result["filename"] == "who-signed-the-lease"
    assert result["fmt"] == "pdf"
    assert result["size_bytes"] == 20480
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_history_serialization.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.history'`

- [ ] **Step 3: Write the implementation**

Create an empty `backend/app/history/__init__.py`, then `backend/app/history/serialization.py`:

```python
"""Pure record -> response-dict shaping for the History page. No DB access
here so it stays unit-testable with plain objects (same split as
app/ingestion/folder_status.py and its _query counterpart).

The list endpoint uses the summary shape and the detail endpoint the full
one: history lists can get long, and shipping every stored answer body
down just to render a list of questions is wasteful.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def query_record_to_summary(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "question": record.question,
        "grounded": record.grounded,
        "source_count": len(record.sources or []),
        "filter_folders": record.filter_folders or [],
        "filter_author": record.filter_author,
        "filter_title": record.filter_title,
        "attached_filenames": record.attached_filenames or [],
        "created_at": _iso(record.created_at),
    }


def query_record_to_detail(record: Any) -> dict[str, Any]:
    return {
        **query_record_to_summary(record),
        "answer": record.answer,
        "sources": record.sources or [],
        "cross_doc": record.cross_doc,
        "statistical": record.statistical,
    }


def export_record_to_dict(record: Any) -> dict[str, Any]:
    # stored_path is deliberately omitted: it's an absolute server path.
    # Downloads go through /history/exports/{id}/download instead.
    return {
        "id": record.id,
        "query_history_id": record.query_history_id,
        "filename": record.filename,
        "fmt": record.fmt,
        "size_bytes": record.size_bytes,
        "created_at": _iso(record.created_at),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_history_serialization.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/history/ backend/tests/test_history_serialization.py
git commit -m "feat: add history record serialization"
```

---

### Task 3: History store + recording on query/export

**Files:**
- Create: `backend/app/history/store.py`
- Modify: `backend/app/main.py` (the `/query`, `/query/upload` and `/export` endpoints)

**Interfaces:**
- Consumes: models from Task 1.
- Produces: `record_query(...) -> int`, `record_export(...) -> int`, `list_queries`, `get_query`, `delete_query`, `list_exports`, `get_export` — all used by Task 4. Also adds `history_id` to the `/query` and `/query/upload` JSON responses, which Task 9's frontend relies on.

- [ ] **Step 1: Write the store module**

Create `backend/app/history/store.py`:

```python
"""DB access for query/export history. Thin wrappers around SQLAlchemy —
the shaping logic they feed lives in serialization.py and is tested
there; this layer is covered by the import smoke test and the end-to-end
pass, matching how the rest of the DB code in this project is handled.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExportHistoryRecord, QueryHistoryRecord


def record_query(
    session: Session,
    *,
    question: str,
    result: dict,
    folders: list[str] | None = None,
    author: str | None = None,
    title: str | None = None,
    attached_filenames: list[str] | None = None,
) -> int:
    """Stores a completed query result and returns the new row's id, which
    the endpoint hands back to the client so a later /export can link to
    the query it came from."""
    record = QueryHistoryRecord(
        question=question,
        answer=result.get("answer", ""),
        sources=result.get("sources") or [],
        grounded=bool(result.get("grounded", False)),
        cross_doc=result.get("cross_doc"),
        statistical=result.get("statistical"),
        filter_folders=folders or [],
        filter_author=author,
        filter_title=title,
        attached_filenames=attached_filenames or [],
    )
    session.add(record)
    session.commit()
    return record.id


def record_export(
    session: Session,
    *,
    query_history_id: int | None,
    filename: str,
    fmt: str,
    stored_path: Path,
) -> int:
    size_bytes = stored_path.stat().st_size if stored_path.exists() else 0
    record = ExportHistoryRecord(
        query_history_id=query_history_id,
        filename=filename,
        fmt=fmt,
        stored_path=str(stored_path),
        size_bytes=size_bytes,
    )
    session.add(record)
    session.commit()
    return record.id


def list_queries(session: Session, limit: int = 50, offset: int = 0) -> list[QueryHistoryRecord]:
    stmt = (
        select(QueryHistoryRecord)
        .order_by(QueryHistoryRecord.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.execute(stmt).scalars())


def get_query(session: Session, history_id: int) -> QueryHistoryRecord | None:
    return session.get(QueryHistoryRecord, history_id)


def delete_query(session: Session, history_id: int) -> bool:
    record = session.get(QueryHistoryRecord, history_id)
    if record is None:
        return False
    session.delete(record)
    session.commit()
    return True


def list_exports(session: Session, limit: int = 50, offset: int = 0) -> list[ExportHistoryRecord]:
    stmt = (
        select(ExportHistoryRecord)
        .order_by(ExportHistoryRecord.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.execute(stmt).scalars())


def get_export(session: Session, export_id: int) -> ExportHistoryRecord | None:
    return session.get(ExportHistoryRecord, export_id)
```

- [ ] **Step 2: Record history in `/query`**

In `backend/app/main.py`, replace the body of `query_endpoint` (currently at line 159-168). Note the added `session` dependency — the endpoint does not take one today:

```python
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
```

- [ ] **Step 3: Record history in `/query/upload`**

In the same file, change `query_upload_endpoint`'s signature to accept a session and record the result. Replace its `return` block (currently lines 209-215):

```python
async def query_upload_endpoint(
    question: str = Form(...),
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
) -> dict:
```

```python
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
        attached_filenames=[f.filename or "upload" for f in files],
    )
    return {**payload, "history_id": history_id}
```

- [ ] **Step 4: Record history in `/export`**

In `ExportRequest`, add the optional link field:

```python
class ExportRequest(BaseModel):
    relative_path: str | None = None
    content: str | None = None
    filename: str = "export"
    fmt: str = "pdf"
    # Set by the dashboard from the /query response so the export shows up
    # on the History page linked to the query that produced it.
    history_id: int | None = None
```

Then add a session dependency to `export_endpoint` and record the export just before returning:

```python
@app.post("/export", dependencies=[Depends(require_bearer_token)])
def export_endpoint(payload: ExportRequest, session: Session = Depends(get_session)) -> FileResponse:
```

```python
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
```

- [ ] **Step 5: Verify the app still imports and routes are intact**

Run: `cd backend && python -c "import app.main; print(len(app.main.app.routes), 'routes')"`
Expected: a route count printed with no exception. If it raises, the edit broke indentation — a bug of exactly this kind was caught this way before.

- [ ] **Step 6: Commit**

```bash
git add backend/app/history/store.py backend/app/main.py
git commit -m "feat: record query and export history server-side"
```

---

### Task 4: History read endpoints

**Files:**
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `app.history.store` functions and `app.history.serialization` shapers from Tasks 2-3.
- Produces: `GET /history/queries`, `GET /history/queries/{id}`, `DELETE /history/queries/{id}`, `GET /history/exports`, `GET /history/exports/{id}/download` — consumed by Task 12's History page.

- [ ] **Step 1: Add the endpoints**

Insert these in `backend/app/main.py` after `export_endpoint` and **before** the SPA catch-all route:

```python
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
```

- [ ] **Step 2: Verify the routes registered in the right order**

Run:
```bash
cd backend && python -c "
import app.main
paths = [getattr(r, 'path', '') for r in app.main.app.routes]
assert '/history/queries' in paths, paths
assert paths.index('/history/queries') < paths.index('/{full_path:path}'), 'route shadowed by SPA catch-all'
print('ok')
"
```
Expected: `ok`. A failure here means the endpoints were added after the catch-all and would return HTML instead of JSON.

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: add history read and re-download endpoints"
```

---

### Task 5: Overview stats

**Files:**
- Create: `backend/app/stats.py`
- Create: `backend/app/stats_query.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_stats.py`

**Interfaces:**
- Consumes: models from Task 1, `list_top_level_folders(raw_dir: Path) -> list[str]` from `app.ingestion.folders`.
- Produces: `ActivityEvent` dataclass, `merge_recent_activity(events, limit=10)`, `build_overview(...)`, `get_overview_stats(session, raw_dir)`, and `GET /stats` — consumed by Task 10's Overview page.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_stats.py`:

```python
from datetime import datetime, timedelta, timezone

from app.stats import ActivityEvent, build_overview, merge_recent_activity

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def _event(kind: str, label: str, minutes_ago: int) -> ActivityEvent:
    return ActivityEvent(type=kind, label=label, at=NOW - timedelta(minutes=minutes_ago))


def test_activity_feed_interleaves_sources_newest_first():
    events = [
        _event("ingest", "lease.pdf", 30),
        _event("query", "who signed it?", 5),
        _event("export", "report.pdf", 15),
    ]

    feed = merge_recent_activity(events)

    assert [e["type"] for e in feed] == ["query", "export", "ingest"]


def test_activity_feed_is_capped():
    events = [_event("ingest", f"file{i}.pdf", i) for i in range(25)]

    feed = merge_recent_activity(events, limit=10)

    assert len(feed) == 10
    assert feed[0]["label"] == "file0.pdf"


def test_activity_entries_carry_iso_timestamps():
    feed = merge_recent_activity([_event("query", "q", 0)])

    assert feed[0]["at"] == NOW.isoformat()


def test_overview_totals_documents_across_statuses():
    overview = build_overview(
        status_counts={"discovered": 3, "converted": 5, "summarized": 2},
        chunk_count=140,
        storage_bytes=2048,
        folder_count=4,
        query_count=9,
        export_count=2,
        activity=[],
    )

    assert overview["documents"]["total"] == 10
    assert overview["documents"]["converted"] == 5
    assert overview["chunks"] == 140
    assert overview["folders"] == 4
    assert overview["queries_total"] == 9
    assert overview["exports_total"] == 2


def test_overview_reports_zero_for_statuses_that_have_no_rows():
    overview = build_overview(
        status_counts={"discovered": 1},
        chunk_count=0,
        storage_bytes=0,
        folder_count=0,
        query_count=0,
        export_count=0,
        activity=[],
    )

    assert overview["documents"]["failed"] == 0
    assert overview["documents"]["summarized"] == 0
    assert overview["documents"]["total"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_stats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.stats'`

- [ ] **Step 3: Write the pure stats module**

Create `backend/app/stats.py`:

```python
"""Pure aggregation for the Overview page. Takes already-fetched counts
as arguments so it unit-tests without a database — the DB fetching lives
in stats_query.py (same split as app/ingestion/folder_status.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ActivityEvent:
    type: str  # "ingest" | "query" | "export"
    label: str
    at: datetime


def merge_recent_activity(events: list[ActivityEvent], limit: int = 10) -> list[dict[str, Any]]:
    """Interleaves per-source event lists into one newest-first feed."""
    ordered = sorted(events, key=lambda e: e.at, reverse=True)[:limit]
    return [{"type": e.type, "label": e.label, "at": e.at.isoformat()} for e in ordered]


def build_overview(
    *,
    status_counts: dict[str, int],
    chunk_count: int,
    storage_bytes: int,
    folder_count: int,
    query_count: int,
    export_count: int,
    activity: list[ActivityEvent],
) -> dict[str, Any]:
    return {
        "documents": {
            "total": sum(status_counts.values()),
            "discovered": status_counts.get("discovered", 0),
            "converted": status_counts.get("converted", 0),
            "summarized": status_counts.get("summarized", 0),
            "failed": status_counts.get("failed", 0),
        },
        "chunks": chunk_count,
        "storage_bytes": storage_bytes,
        "folders": folder_count,
        "queries_total": query_count,
        "exports_total": export_count,
        "recent_activity": merge_recent_activity(activity),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_stats.py -v`
Expected: 5 passed

- [ ] **Step 5: Write the DB wrapper**

Create `backend/app/stats_query.py`:

```python
"""DB fetching for the Overview page — pairs with the pure shaping in
app/stats.py."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingestion.folders import list_top_level_folders
from app.models import ChunkRecord, ExportHistoryRecord, FileRecord, QueryHistoryRecord
from app.stats import ActivityEvent, build_overview

_ACTIVITY_PER_SOURCE = 10


def get_overview_stats(session: Session, raw_dir: Path) -> dict:
    status_counts = {
        status: count
        for status, count in session.execute(
            select(FileRecord.status, func.count()).group_by(FileRecord.status)
        ).all()
    }

    chunk_count = session.execute(select(func.count()).select_from(ChunkRecord)).scalar_one()
    storage_bytes = session.execute(
        select(func.coalesce(func.sum(FileRecord.size_bytes), 0))
    ).scalar_one()
    query_count = session.execute(select(func.count()).select_from(QueryHistoryRecord)).scalar_one()
    export_count = session.execute(
        select(func.count()).select_from(ExportHistoryRecord)
    ).scalar_one()

    activity: list[ActivityEvent] = []

    for path, discovered_at in session.execute(
        select(FileRecord.path, FileRecord.discovered_at)
        .order_by(FileRecord.discovered_at.desc())
        .limit(_ACTIVITY_PER_SOURCE)
    ).all():
        activity.append(ActivityEvent(type="ingest", label=Path(path).name, at=discovered_at))

    for question, created_at in session.execute(
        select(QueryHistoryRecord.question, QueryHistoryRecord.created_at)
        .order_by(QueryHistoryRecord.created_at.desc())
        .limit(_ACTIVITY_PER_SOURCE)
    ).all():
        activity.append(ActivityEvent(type="query", label=question, at=created_at))

    for filename, fmt, created_at in session.execute(
        select(
            ExportHistoryRecord.filename,
            ExportHistoryRecord.fmt,
            ExportHistoryRecord.created_at,
        )
        .order_by(ExportHistoryRecord.created_at.desc())
        .limit(_ACTIVITY_PER_SOURCE)
    ).all():
        activity.append(ActivityEvent(type="export", label=f"{filename}.{fmt}", at=created_at))

    return build_overview(
        status_counts=status_counts,
        chunk_count=chunk_count,
        storage_bytes=storage_bytes,
        folder_count=len(list_top_level_folders(raw_dir)),
        query_count=query_count,
        export_count=export_count,
        activity=activity,
    )
```

- [ ] **Step 6: Add the endpoint**

In `backend/app/main.py`, before the SPA catch-all:

```python
@app.get("/stats", dependencies=[Depends(require_bearer_token)])
def stats_endpoint(session: Session = Depends(get_session)) -> dict:
    """Overview metrics for the dashboard home page."""
    from app.stats_query import get_overview_stats

    return get_overview_stats(session, Path(settings.data_dir) / "raw")
```

- [ ] **Step 7: Verify the import and route**

Run: `cd backend && python -c "
import app.main
assert '/stats' in [getattr(r,'path','') for r in app.main.app.routes]
print('ok')"`
Expected: `ok`

- [ ] **Step 8: Commit**

```bash
git add backend/app/stats.py backend/app/stats_query.py backend/app/main.py backend/tests/test_stats.py
git commit -m "feat: add overview stats endpoint"
```

---

### Task 6: Corpus-wide documents listing

**Files:**
- Create: `backend/app/ingestion/documents_query.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_documents_query.py`

**Interfaces:**
- Consumes: `build_folder_like_patterns(raw_dir: str, folders: list[str]) -> list[str]` from `app.search.folder_filter`.
- Produces: `relative_to_raw(path, raw_dir) -> str`, `list_documents(session, *, raw_dir, folder, status, search, limit, offset) -> tuple[list[dict], int]`, and `GET /documents` — consumed by Task 11's Documents page.

- [ ] **Step 1: Write the failing test for the path helper**

Create `backend/tests/test_documents_query.py`:

```python
from app.ingestion.documents_query import relative_to_raw


def test_strips_posix_raw_prefix():
    assert (
        relative_to_raw("/data/pipeline/raw/contracts/lease.pdf", "/data/pipeline/raw")
        == "contracts/lease.pdf"
    )


def test_strips_windows_raw_prefix():
    assert (
        relative_to_raw(r"D:\data\raw\contracts\lease.pdf", r"D:\data\raw")
        == r"contracts\lease.pdf"
    )


def test_tolerates_a_trailing_separator_on_raw_dir():
    assert relative_to_raw("/data/raw/a.pdf", "/data/raw/") == "a.pdf"


def test_leaves_unrelated_paths_untouched():
    assert relative_to_raw("/somewhere/else/a.pdf", "/data/raw") == "/somewhere/else/a.pdf"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_documents_query.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ingestion.documents_query'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/ingestion/documents_query.py`:

```python
"""Corpus-wide file listing for the Documents page. One call across all
folders — the Documents table would otherwise need N round trips to the
per-folder /folders/{name}/files endpoint.

Folder scoping reuses build_folder_like_patterns rather than building its
own LIKE pattern: that helper already handles PostgreSQL treating a
backslash as its escape character, which silently broke folder filtering
on Windows paths before.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import FileRecord
from app.search.folder_filter import build_folder_like_patterns


def relative_to_raw(path: str, raw_dir: str) -> str:
    """Trims the raw/ prefix so the UI shows "contracts/lease.pdf" rather
    than the full container path."""
    base = raw_dir.rstrip("/\\")
    for separator in ("/", "\\"):
        prefix = base + separator
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def list_documents(
    session: Session,
    *,
    raw_dir: str,
    folder: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict], int]:
    conditions = []

    if folder:
        patterns = build_folder_like_patterns(raw_dir, [folder])
        if patterns:
            conditions.append(or_(*(FileRecord.path.like(p) for p in patterns)))

    if status:
        conditions.append(FileRecord.status == status)

    if search:
        like = f"%{search}%"
        conditions.append(
            or_(
                FileRecord.path.ilike(like),
                FileRecord.title.ilike(like),
                FileRecord.author.ilike(like),
            )
        )

    rows_stmt = select(FileRecord)
    count_stmt = select(func.count()).select_from(FileRecord)
    if conditions:
        rows_stmt = rows_stmt.where(*conditions)
        count_stmt = count_stmt.where(*conditions)

    total = session.execute(count_stmt).scalar_one()
    rows = session.execute(
        rows_stmt.order_by(FileRecord.discovered_at.desc()).limit(limit).offset(offset)
    ).scalars()

    documents = [
        {
            "id": row.id,
            "path": relative_to_raw(row.path, raw_dir),
            "title": row.title,
            "author": row.author,
            "mime_type": row.mime_type,
            "size_bytes": row.size_bytes,
            "page_count": row.page_count,
            "status": row.status,
            "queue": row.queue,
            "discovered_at": row.discovered_at.isoformat() if row.discovered_at else None,
            "doc_created_at": row.doc_created_at.isoformat() if row.doc_created_at else None,
        }
        for row in rows
    ]
    return documents, total
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_documents_query.py -v`
Expected: 4 passed

- [ ] **Step 5: Add the endpoint**

In `backend/app/main.py`, before the SPA catch-all:

```python
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
```

- [ ] **Step 6: Run the whole backend suite**

Run: `cd backend && python -m pytest -q`
Expected: all tests pass, including the pre-existing ones.

- [ ] **Step 7: Commit**

```bash
git add backend/app/ingestion/documents_query.py backend/app/main.py backend/tests/test_documents_query.py
git commit -m "feat: add corpus-wide documents listing endpoint"
```

---

### Task 7: Denser design tokens + API client

**Files:**
- Modify: `dashboard/src/ui.ts`
- Modify: `dashboard/src/api/client.ts`

**Interfaces:**
- Consumes: the endpoints from Tasks 4-6.
- Produces: revised shared class constants plus `tableHeader`, `tableCell`, `tile` additions; and API methods `stats()`, `listDocuments()`, `listQueryHistory()`, `getQueryHistory()`, `deleteQueryHistory()`, `listExportHistory()`, `downloadHistoryExport()` with the TS interfaces `OverviewStats`, `DocumentRow`, `QueryHistorySummary`, `QueryHistoryDetail`, `ExportHistoryRow` — used by every later frontend task.

**Before starting:** load the `frontend-design` skill.

- [ ] **Step 1: Tighten the shared class constants**

Replace the contents of `dashboard/src/ui.ts`:

```typescript
// Shared class strings for the dense "console" look — see index.css for
// the token definitions these arbitrary-value classes reference. The
// tokens themselves are unchanged (dark mode is defined entirely through
// them); what changed here is sizing and spacing, tightened from the
// original airy card layout toward an information-dense dashboard.
//
// Centralized so the look doesn't drift between pages: revise here, not
// per-page.

export const input =
  "rounded border border-[var(--line)] bg-[var(--paper)] px-2.5 py-1.5 text-[13px] text-[var(--ink)] placeholder:text-[var(--ink-soft)] focus:border-[var(--index)] focus:outline-none transition-colors";

export const button =
  "rounded bg-[var(--index)] px-3 py-1.5 text-[13px] font-medium text-[var(--paper)] hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 transition-opacity";

export const buttonSecondary =
  "rounded border border-[var(--line)] px-2.5 py-1 text-xs text-[var(--ink)] hover:border-[var(--index)] hover:text-[var(--index)] disabled:opacity-40 transition-colors";

export const card =
  "rounded border border-[var(--line)] bg-[var(--paper)] p-4";

export const tile =
  "rounded border border-[var(--line)] bg-[var(--paper)] px-4 py-3";

export const tableHeader =
  "font-mono text-[11px] uppercase tracking-wider text-[var(--ink-soft)] text-left font-normal py-2 pr-4 border-b border-[var(--line)]";

export const tableCell = "py-2 pr-4 text-[13px] border-b border-[var(--line)]";

export const muted = "text-[var(--ink-soft)]";

export const errorText = "font-mono text-xs text-[var(--danger)]";

export const label = "font-mono text-[11px] uppercase tracking-wider text-[var(--ink-soft)]";

export const pageTitle = "font-display text-lg font-semibold tracking-tight";
```

- [ ] **Step 2: Add the types and API methods**

In `dashboard/src/api/client.ts`, add these interfaces next to the existing `FolderStatus`/`QueryResult`:

```typescript
export interface OverviewStats {
  documents: {
    total: number;
    discovered: number;
    converted: number;
    summarized: number;
    failed: number;
  };
  chunks: number;
  storage_bytes: number;
  folders: number;
  queries_total: number;
  exports_total: number;
  recent_activity: { type: string; label: string; at: string }[];
}

export interface DocumentRow {
  id: number;
  path: string;
  title: string | null;
  author: string | null;
  mime_type: string;
  size_bytes: number;
  page_count: number | null;
  status: string;
  queue: string;
  discovered_at: string | null;
  doc_created_at: string | null;
}

export interface QueryHistorySummary {
  id: number;
  question: string;
  grounded: boolean;
  source_count: number;
  filter_folders: string[];
  filter_author: string | null;
  filter_title: string | null;
  attached_filenames: string[];
  created_at: string | null;
}

export interface QueryHistoryDetail extends QueryHistorySummary {
  answer: string;
  sources: string[];
  cross_doc: { answer: string; sources: string[] } | null;
  statistical: { answer: string; correlation_summary: string } | null;
}

export interface ExportHistoryRow {
  id: number;
  query_history_id: number | null;
  filename: string;
  fmt: string;
  size_bytes: number;
  created_at: string | null;
}
```

Extend `QueryResult` with the new field returned by Task 3:

```typescript
export interface QueryResult {
  question: string;
  answer: string;
  sources: string[];
  grounded: boolean;
  cross_doc: { answer: string; sources: string[] } | null;
  statistical: { answer: string; correlation_summary: string } | null;
  history_id?: number;
}
```

Then add these methods inside the `api` object:

```typescript
  stats: () => request<OverviewStats>("/stats"),

  listDocuments: (opts: { folder?: string; status?: string; q?: string } = {}) => {
    const params = new URLSearchParams();
    if (opts.folder) params.set("folder", opts.folder);
    if (opts.status) params.set("status", opts.status);
    if (opts.q) params.set("q", opts.q);
    const qs = params.toString();
    return request<{ documents: DocumentRow[]; total: number }>(
      qs ? `/documents?${qs}` : "/documents",
    );
  },

  listQueryHistory: () => request<{ queries: QueryHistorySummary[] }>("/history/queries"),

  getQueryHistory: (id: number) => request<QueryHistoryDetail>(`/history/queries/${id}`),

  deleteQueryHistory: (id: number) =>
    request<{ deleted: number }>(`/history/queries/${id}`, { method: "DELETE" }),

  listExportHistory: () => request<{ exports: ExportHistoryRow[] }>("/history/exports"),
```

- [ ] **Step 3: Add the export re-download helper**

At the bottom of `client.ts`, next to `downloadExport`:

```typescript
/** Re-downloads a previously generated export straight from the server
 * rather than re-rendering it — the file is still on disk under
 * exports/. Returns an error message if the server no longer has it. */
export async function downloadHistoryExport(row: ExportHistoryRow): Promise<void> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${BASE_URL}/history/exports/${row.id}/download`, { headers });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // not JSON; keep statusText
    }
    throw new ApiError(response.status, detail);
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${row.filename}.${row.fmt}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
```

- [ ] **Step 4: Pass `history_id` through on export**

Change `downloadExport`'s signature and body in the same file so exports link back to their query:

```typescript
export async function downloadExport(
  content: string,
  filename: string,
  fmt: "pdf" | "docx" = "pdf",
  historyId?: number,
): Promise<void> {
```

and inside it, change the body to:

```typescript
    body: JSON.stringify({ content, filename, fmt, history_id: historyId ?? null }),
```

- [ ] **Step 5: Type-check**

Run: `cd dashboard && npx tsc --noEmit`
Expected: exit code 0. Errors about `handleDownload` accepting `"json"` are expected here — fix them by narrowing that signature in `Ask.tsx` to `"pdf" | "docx"`, which matches the buttons already rendered.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/ui.ts dashboard/src/api/client.ts dashboard/src/pages/Ask.tsx
git commit -m "feat: denser design tokens and dashboard API client methods"
```

---

### Task 8: Sidebar shell and routing

**Files:**
- Create: `dashboard/src/components/Sidebar.tsx`
- Modify: `dashboard/src/App.tsx`
- Create: `dashboard/src/pages/Overview.tsx` (placeholder, filled in Task 10)
- Create: `dashboard/src/pages/Documents.tsx` (placeholder, filled in Task 11)
- Create: `dashboard/src/pages/History.tsx` (placeholder, filled in Task 12)

**Interfaces:**
- Consumes: `ui.ts` constants from Task 7, `useAuth()` from `dashboard/src/auth/AuthContext`.
- Produces: the five routes `/`, `/ask`, `/documents`, `/history`, `/ingestion`, and a `<Sidebar />` component. Tasks 10-13 fill the placeholder pages.

- [ ] **Step 1: Create the sidebar**

Create `dashboard/src/components/Sidebar.tsx`:

```tsx
import { NavLink } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/ask", label: "Ask", end: false },
  { to: "/documents", label: "Documents", end: false },
  { to: "/history", label: "History", end: false },
  { to: "/ingestion", label: "Ingestion", end: false },
];

const linkClasses = ({ isActive }: { isActive: boolean }) =>
  `block rounded px-3 py-1.5 text-[13px] transition-colors ${
    isActive
      ? "bg-[var(--index-soft)] font-medium text-[var(--index)]"
      : "text-[var(--ink-soft)] hover:bg-[var(--index-soft)] hover:text-[var(--ink)]"
  }`;

export function Sidebar() {
  const { logout } = useAuth();

  return (
    <aside className="flex shrink-0 flex-col border-b border-[var(--line)] bg-[var(--paper)] px-4 py-4 lg:h-screen lg:w-[220px] lg:border-b-0 lg:border-r">
      <span className="font-display px-3 text-sm font-semibold tracking-tight">
        DOC<span className="text-[var(--index)]">/</span>INDEX
      </span>

      <nav className="mt-5 flex gap-1 lg:mt-6 lg:flex-col">
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={linkClasses}>
            {item.label}
          </NavLink>
        ))}
      </nav>

      <button
        onClick={logout}
        className="font-mono mt-4 px-3 text-left text-[11px] uppercase tracking-wider text-[var(--ink-soft)] hover:text-[var(--ink)] lg:mt-auto"
      >
        Log out
      </button>
    </aside>
  );
}
```

- [ ] **Step 2: Create the three placeholder pages**

`dashboard/src/pages/Overview.tsx`:

```tsx
export function Overview() {
  return <div>Overview</div>;
}
```

`dashboard/src/pages/Documents.tsx`:

```tsx
export function Documents() {
  return <div>Documents</div>;
}
```

`dashboard/src/pages/History.tsx`:

```tsx
export function History() {
  return <div>History</div>;
}
```

- [ ] **Step 3: Rewrite App.tsx around the sidebar**

Replace `dashboard/src/App.tsx`:

```tsx
import { Navigate, Route, Routes } from "react-router-dom";
import { Sidebar } from "./components/Sidebar";
import { useAuth } from "./auth/AuthContext";
import { Ask } from "./pages/Ask";
import { Documents } from "./pages/Documents";
import { History } from "./pages/History";
import { Login } from "./pages/Login";
import { Overview } from "./pages/Overview";
import { Status } from "./pages/Status";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { token } = useAuth();
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-[var(--paper)] text-[13px] text-[var(--ink)] lg:flex">
      <Sidebar />
      <main className="w-full max-w-7xl px-6 py-7">{children}</main>
    </div>
  );
}

function guarded(element: React.ReactNode) {
  return (
    <RequireAuth>
      <Layout>{element}</Layout>
    </RequireAuth>
  );
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={guarded(<Overview />)} />
      <Route path="/ask" element={guarded(<Ask />)} />
      <Route path="/documents" element={guarded(<Documents />)} />
      <Route path="/history" element={guarded(<History />)} />
      <Route path="/ingestion" element={guarded(<Status />)} />
      {/* Old bookmarks from the two-page layout. */}
      <Route path="/status" element={<Navigate to="/ingestion" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
```

- [ ] **Step 4: Type-check**

Run: `cd dashboard && npx tsc --noEmit`
Expected: exit code 0

- [ ] **Step 5: Verify in the browser**

Run `cd dashboard && npm run dev`, log in, and click every sidebar item. Expected: all five routes resolve (three show placeholder text), the active item is highlighted, `/status` redirects to `/ingestion`, and the layout stacks rather than overflowing at a narrow window width.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/Sidebar.tsx dashboard/src/App.tsx dashboard/src/pages/Overview.tsx dashboard/src/pages/Documents.tsx dashboard/src/pages/History.tsx
git commit -m "feat: sidebar shell with five dashboard routes"
```

---

### Task 9: Extract ResultCard, refactor Ask

**Files:**
- Create: `dashboard/src/components/ResultCard.tsx`
- Modify: `dashboard/src/pages/Ask.tsx`

**Interfaces:**
- Consumes: `QueryResult` / `QueryHistoryDetail` types and `downloadExport` from Task 7.
- Produces: `<ResultCard result={...} onDownload={...} downloading={...} />` — Task 12's History page renders stored results with the same component, which is why this extraction is required rather than cosmetic.

- [ ] **Step 1: Extract the result rendering**

Create `dashboard/src/components/ResultCard.tsx`. Move the answer/sources/cross-doc/statistical rendering out of `Ask.tsx` unchanged in structure, parameterized so it works for both a live result and a stored one:

```tsx
import { card, label, muted } from "../ui";

export interface ResultCardData {
  question: string;
  answer: string;
  sources: string[];
  grounded: boolean;
  cross_doc: { answer: string; sources: string[] } | null;
  statistical: { answer: string; correlation_summary: string } | null;
}

/** Strips everything up to and including "/raw/" so citations show a
 * readable "contracts/q2_update.txt" instead of the full container path. */
export function shortenSource(path: string): string {
  const marker = "/raw/";
  const idx = path.indexOf(marker);
  return idx === -1 ? path : path.slice(idx + marker.length);
}

/** The signature element: clipped-corner index tabs for citations. Kept
 * from the original archive identity because citations are the heart of
 * a RAG result. */
function SourceTabs({ sources }: { sources: string[] }) {
  if (sources.length === 0) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {sources.map((source, i) => (
        <span
          key={source}
          className="font-mono border border-[var(--line)] bg-[var(--locator-soft)] px-2 py-0.5 text-[11px] text-[var(--ink)]"
          style={{ clipPath: "polygon(0 0, calc(100% - 7px) 0, 100% 7px, 100% 100%, 0 100%)" }}
        >
          [{i + 1}] {shortenSource(source)}
        </span>
      ))}
    </div>
  );
}

export function ResultCard({
  result,
  actions,
}: {
  result: ResultCardData;
  actions?: React.ReactNode;
}) {
  return (
    <div className={card}>
      <p className={label}>Answer</p>
      {!result.grounded && (
        <p className={`font-mono mt-1 text-[11px] ${muted}`}>
          Not grounded — no corpus source matched closely enough.
        </p>
      )}
      <p className="mt-2 whitespace-pre-wrap">{result.answer}</p>
      <SourceTabs sources={result.sources} />

      {result.cross_doc && (
        <div className="mt-5 border-t border-[var(--line)] pt-4">
          <p className={label}>Cross-document findings</p>
          <p className="mt-2 whitespace-pre-wrap">{result.cross_doc.answer}</p>
          <SourceTabs sources={result.cross_doc.sources} />
        </div>
      )}

      {result.statistical && (
        <div className="mt-5 border-t border-[var(--line)] pt-4">
          <p className={label}>Statistical findings</p>
          <p className="mt-2 whitespace-pre-wrap">{result.statistical.answer}</p>
          <pre className="font-mono mt-2 whitespace-pre-wrap rounded border border-[var(--line)] bg-[var(--locator-soft)] p-2.5 text-[11px]">
            {result.statistical.correlation_summary}
          </pre>
        </div>
      )}

      {actions && (
        <div className="mt-5 flex items-center gap-2 border-t border-[var(--line)] pt-4">{actions}</div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Rewire Ask to use it**

In `dashboard/src/pages/Ask.tsx`:
- Delete the local `shortenSource` and `SourceTabs` definitions and the inline result markup, importing `ResultCard` and `shortenSource` from `../components/ResultCard` instead (`buildMarkdown` still needs `shortenSource`).
- Render the result as `<ResultCard result={result} actions={downloadButtons} />`, where `downloadButtons` is the existing PDF/DOCX button row.
- Pass the history id through on export:

```tsx
await downloadExport(buildMarkdown(result), slugForFilename(result.question), fmt, result.history_id);
```

- [ ] **Step 3: Convert the folder sidebar into a horizontal scope bar**

Replace the `<FolderSidebar>` column in `Ask.tsx` with a single-row scope strip above the question input, keeping the same `folders`/`selectedFolders`/`toggleFolder`/`onCreated` state and the same polling — only the layout changes:

```tsx
<div className="flex flex-wrap items-center gap-1.5 border-b border-[var(--line)] pb-3">
  <span className={`${label} mr-1`}>Scope</span>
  {folders.map((f) => {
    const selected = selectedFolders.includes(f.name);
    return (
      <button
        key={f.name}
        onClick={() => toggleFolder(f.name)}
        className={`rounded border px-2 py-0.5 text-xs transition-colors ${
          selected
            ? "border-[var(--index)] bg-[var(--index-soft)] text-[var(--index)]"
            : "border-[var(--line)] text-[var(--ink-soft)] hover:text-[var(--ink)]"
        }`}
      >
        {f.name}
        {f.processing && <span className="ml-1 text-[var(--locator)]">●</span>}
        {f.has_failures && <span className="ml-1 text-[var(--danger)]">●</span>}
      </button>
    );
  })}
  {folders.length === 0 && <span className={muted}>No folders yet</span>}
</div>
```

Keep the create-folder control, moved to the right end of this strip.

- [ ] **Step 4: Type-check**

Run: `cd dashboard && npx tsc --noEmit`
Expected: exit code 0

- [ ] **Step 5: Verify in the browser**

With the dev server running, open `/ask`. Expected: the scope strip lists folders and toggles them, asking a question still returns a result rendered through `ResultCard` with citation tabs intact, `+` attach still works, and the PDF/DOCX buttons still download.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/ResultCard.tsx dashboard/src/pages/Ask.tsx
git commit -m "refactor: extract ResultCard and convert Ask folder sidebar to a scope bar"
```

---

### Task 10: Overview page

**Files:**
- Modify: `dashboard/src/pages/Overview.tsx`
- Create: `dashboard/src/components/KpiTile.tsx`
- Create: `dashboard/src/components/IngestionBar.tsx`

**Interfaces:**
- Consumes: `api.stats()` and `OverviewStats` from Task 7.
- Produces: nothing other tasks depend on.

**Before starting:** load the `dataviz` skill — this task builds a KPI tile row and a stacked bar.

- [ ] **Step 1: Create the KPI tile**

Create `dashboard/src/components/KpiTile.tsx`:

```tsx
import { label, tile } from "../ui";

export function KpiTile({
  name,
  value,
  sub,
}: {
  name: string;
  value: string | number;
  sub?: string;
}) {
  return (
    <div className={tile}>
      <p className={label}>{name}</p>
      <p className="font-display mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="mt-0.5 text-[11px] text-[var(--ink-soft)]">{sub}</p>}
    </div>
  );
}
```

- [ ] **Step 2: Create the stacked ingestion bar**

Create `dashboard/src/components/IngestionBar.tsx`. Plain CSS widths — no chart library:

```tsx
import { label } from "../ui";

const SEGMENTS = [
  { key: "summarized", name: "Summarized", color: "var(--signal)" },
  { key: "converted", name: "Converted", color: "var(--index)" },
  { key: "discovered", name: "Discovered", color: "var(--locator)" },
  { key: "failed", name: "Failed", color: "var(--danger)" },
] as const;

export function IngestionBar({
  counts,
}: {
  counts: { discovered: number; converted: number; summarized: number; failed: number; total: number };
}) {
  const total = counts.total || 1;

  return (
    <div>
      <p className={label}>Ingestion status</p>
      <div className="mt-2 flex h-2 w-full overflow-hidden rounded-full bg-[var(--line)]">
        {SEGMENTS.map((segment) => {
          const value = counts[segment.key];
          if (value === 0) return null;
          return (
            <div
              key={segment.key}
              style={{ width: `${(value / total) * 100}%`, background: segment.color }}
              title={`${segment.name}: ${value}`}
            />
          );
        })}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {SEGMENTS.map((segment) => (
          <span key={segment.key} className="flex items-center gap-1.5 text-[11px]">
            <span className="h-2 w-2 rounded-full" style={{ background: segment.color }} />
            <span className="text-[var(--ink-soft)]">{segment.name}</span>
            <span className="font-mono tabular-nums">{counts[segment.key]}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Build the Overview page**

Replace `dashboard/src/pages/Overview.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api, ApiError, type OverviewStats } from "../api/client";
import { IngestionBar } from "../components/IngestionBar";
import { KpiTile } from "../components/KpiTile";
import { card, errorText, label, muted, pageTitle } from "../ui";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = -1;
  do {
    value /= 1024;
    unitIndex++;
  } while (value >= 1024 && unitIndex < units.length - 1);
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}

export function Overview() {
  const [stats, setStats] = useState<OverviewStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .stats()
      .then(setStats)
      .catch((err) =>
        setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err)),
      );
  }, []);

  if (error) return <p className={errorText}>{error}</p>;
  if (!stats) return <p className={muted}>Loading…</p>;

  return (
    <div>
      <p className={label}>Corpus</p>
      <h1 className={`mt-1 ${pageTitle}`}>Overview</h1>

      <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiTile
          name="Documents"
          value={stats.documents.total}
          sub={`${stats.documents.summarized} summarized`}
        />
        <KpiTile name="Chunks indexed" value={stats.chunks} sub="searchable vectors" />
        <KpiTile name="Storage" value={formatBytes(stats.storage_bytes)} sub={`${stats.folders} folders`} />
        <KpiTile
          name="Queries run"
          value={stats.queries_total}
          sub={`${stats.exports_total} exports`}
        />
      </div>

      <div className={`mt-4 ${card}`}>
        <IngestionBar counts={{ ...stats.documents }} />
      </div>

      <div className={`mt-4 ${card}`}>
        <p className={label}>Recent activity</p>
        {stats.recent_activity.length === 0 ? (
          <p className={`mt-2 ${muted}`}>Nothing yet.</p>
        ) : (
          <ul className="mt-2">
            {stats.recent_activity.map((event, i) => (
              <li
                key={`${event.type}-${event.at}-${i}`}
                className="flex items-baseline gap-3 border-b border-[var(--line)] py-1.5 last:border-b-0"
              >
                <span className="font-mono w-14 shrink-0 text-[11px] uppercase text-[var(--index)]">
                  {event.type}
                </span>
                <span className="flex-1 truncate">{event.label}</span>
                <span className="font-mono shrink-0 text-[11px] text-[var(--ink-soft)]">
                  {formatWhen(event.at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Type-check**

Run: `cd dashboard && npx tsc --noEmit`
Expected: exit code 0

- [ ] **Step 5: Verify in the browser**

Open `/`. Expected: four KPI tiles with real numbers matching what `/folders` reports, a stacked status bar whose segments sum to the document total, and a recent-activity list. Toggle OS dark mode and confirm every element remains legible.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/pages/Overview.tsx dashboard/src/components/KpiTile.tsx dashboard/src/components/IngestionBar.tsx
git commit -m "feat: overview page with KPI tiles and ingestion status bar"
```

---

### Task 11: Documents page

**Files:**
- Modify: `dashboard/src/pages/Documents.tsx`

**Interfaces:**
- Consumes: `api.listDocuments()`, `api.listFolders()`, `DocumentRow` from Task 7.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Build the page**

Replace `dashboard/src/pages/Documents.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api, ApiError, type DocumentRow, type FolderStatus } from "../api/client";
import { errorText, input, label, muted, pageTitle, tableCell, tableHeader } from "../ui";

const STATUSES = ["discovered", "converted", "summarized", "failed"];

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = -1;
  do {
    value /= 1024;
    unitIndex++;
  } while (value >= 1024 && unitIndex < units.length - 1);
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

export function Documents() {
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [total, setTotal] = useState(0);
  const [folders, setFolders] = useState<FolderStatus[]>([]);
  const [folder, setFolder] = useState("");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listFolders().then((r) => setFolders(r.folders)).catch(() => setFolders([]));
  }, []);

  useEffect(() => {
    const handle = setTimeout(() => {
      api
        .listDocuments({ folder: folder || undefined, status: status || undefined, q: search || undefined })
        .then((r) => {
          setDocuments(r.documents);
          setTotal(r.total);
          setError(null);
        })
        .catch((err) =>
          setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err)),
        );
    }, 250);
    return () => clearTimeout(handle);
  }, [folder, status, search]);

  return (
    <div>
      <p className={label}>Corpus</p>
      <h1 className={`mt-1 ${pageTitle}`}>Documents</h1>
      <p className={`mt-1 ${muted}`}>
        Every ingested file and the metadata extracted from it at convert time.
      </p>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <input
          className={`${input} w-60`}
          placeholder="Search path, title or author"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className={input} value={folder} onChange={(e) => setFolder(e.target.value)}>
          <option value="">All folders</option>
          {folders.map((f) => (
            <option key={f.name} value={f.name}>
              {f.name}
            </option>
          ))}
        </select>
        <select className={input} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <span className={`font-mono ml-auto text-[11px] ${muted}`}>{total} files</span>
      </div>

      {error && <p className={`mt-4 ${errorText}`}>{error}</p>}

      <table className="mt-4 w-full border-collapse">
        <thead>
          <tr>
            <th className={tableHeader}>Path</th>
            <th className={tableHeader}>Title</th>
            <th className={tableHeader}>Author</th>
            <th className={tableHeader}>Pages</th>
            <th className={tableHeader}>Size</th>
            <th className={tableHeader}>Status</th>
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => (
            <tr key={doc.id}>
              <td className={`font-mono ${tableCell}`}>{doc.path}</td>
              <td className={tableCell}>{doc.title ?? <span className={muted}>—</span>}</td>
              <td className={tableCell}>{doc.author ?? <span className={muted}>—</span>}</td>
              <td className={`${tableCell} tabular-nums`}>{doc.page_count ?? ""}</td>
              <td className={`${tableCell} tabular-nums`}>{formatBytes(doc.size_bytes)}</td>
              <td className={`font-mono ${tableCell} text-[11px]`}>{doc.status}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {documents.length === 0 && !error && <p className={`mt-4 ${muted}`}>No documents match.</p>}
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd dashboard && npx tsc --noEmit`
Expected: exit code 0

- [ ] **Step 3: Verify in the browser**

Open `/documents`. Expected: the table lists ingested files with title/author where metadata was extracted, the folder and status dropdowns narrow the list, typing in search filters after a short debounce, and the count on the right matches the row count when unfiltered.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/pages/Documents.tsx
git commit -m "feat: documents browser page"
```

---

### Task 12: History page

**Files:**
- Modify: `dashboard/src/pages/History.tsx`

**Interfaces:**
- Consumes: `api.listQueryHistory()`, `api.getQueryHistory()`, `api.deleteQueryHistory()`, `api.listExportHistory()`, `downloadHistoryExport()` from Task 7; `<ResultCard />` from Task 9.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Build the page**

Replace `dashboard/src/pages/History.tsx`:

```tsx
import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  downloadHistoryExport,
  type ExportHistoryRow,
  type QueryHistoryDetail,
  type QueryHistorySummary,
} from "../api/client";
import { ResultCard } from "../components/ResultCard";
import { buttonSecondary, errorText, label, muted, pageTitle, tableCell, tableHeader } from "../ui";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = -1;
  do {
    value /= 1024;
    unitIndex++;
  } while (value >= 1024 && unitIndex < units.length - 1);
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}

function scopeLabel(entry: QueryHistorySummary): string {
  if (entry.attached_filenames.length > 0) return `${entry.attached_filenames.length} attached file(s)`;
  const parts: string[] = [];
  if (entry.filter_folders.length > 0) parts.push(entry.filter_folders.join(", "));
  if (entry.filter_author) parts.push(`author: ${entry.filter_author}`);
  if (entry.filter_title) parts.push(`title: ${entry.filter_title}`);
  return parts.length > 0 ? parts.join(" · ") : "whole corpus";
}

export function History() {
  const [queries, setQueries] = useState<QueryHistorySummary[]>([]);
  const [exports, setExports] = useState<ExportHistoryRow[]>([]);
  const [open, setOpen] = useState<QueryHistoryDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    api.listQueryHistory().then((r) => setQueries(r.queries)).catch(reportError);
    api.listExportHistory().then((r) => setExports(r.exports)).catch(reportError);
  };

  function reportError(err: unknown) {
    setError(err instanceof ApiError ? `${err.status}: ${err.message}` : String(err));
  }

  useEffect(refresh, []);

  const openEntry = async (id: number) => {
    setError(null);
    try {
      setOpen(await api.getQueryHistory(id));
    } catch (err) {
      reportError(err);
    }
  };

  const remove = async (id: number) => {
    setError(null);
    try {
      await api.deleteQueryHistory(id);
      if (open?.id === id) setOpen(null);
      refresh();
    } catch (err) {
      reportError(err);
    }
  };

  const download = async (row: ExportHistoryRow) => {
    setError(null);
    try {
      await downloadHistoryExport(row);
    } catch (err) {
      reportError(err);
    }
  };

  return (
    <div>
      <p className={label}>Activity</p>
      <h1 className={`mt-1 ${pageTitle}`}>History</h1>
      <p className={`mt-1 ${muted}`}>
        Every question asked and every file exported. Opening a past search restores its stored
        answer without re-running the model.
      </p>

      {error && <p className={`mt-4 ${errorText}`}>{error}</p>}

      <p className={`mt-6 ${label}`}>Searches</p>
      <table className="mt-2 w-full border-collapse">
        <thead>
          <tr>
            <th className={tableHeader}>Question</th>
            <th className={tableHeader}>Scope</th>
            <th className={tableHeader}>Sources</th>
            <th className={tableHeader}>When</th>
            <th className={tableHeader}></th>
          </tr>
        </thead>
        <tbody>
          {queries.map((entry) => (
            <tr key={entry.id}>
              <td className={tableCell}>
                <button className="text-left hover:text-[var(--index)]" onClick={() => openEntry(entry.id)}>
                  {entry.question}
                </button>
              </td>
              <td className={`${tableCell} text-[11px] ${muted}`}>{scopeLabel(entry)}</td>
              <td className={`${tableCell} tabular-nums`}>{entry.source_count}</td>
              <td className={`font-mono ${tableCell} text-[11px]`}>{formatWhen(entry.created_at)}</td>
              <td className={tableCell}>
                <button className={buttonSecondary} onClick={() => remove(entry.id)}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {queries.length === 0 && <p className={`mt-3 ${muted}`}>No searches yet.</p>}

      {open && (
        <div className="mt-4">
          <div className="flex items-center justify-between">
            <p className={label}>{open.question}</p>
            <button className={buttonSecondary} onClick={() => setOpen(null)}>
              Close
            </button>
          </div>
          <div className="mt-2">
            <ResultCard result={open} />
          </div>
        </div>
      )}

      <p className={`mt-8 ${label}`}>Exports</p>
      <table className="mt-2 w-full border-collapse">
        <thead>
          <tr>
            <th className={tableHeader}>File</th>
            <th className={tableHeader}>Format</th>
            <th className={tableHeader}>Size</th>
            <th className={tableHeader}>When</th>
            <th className={tableHeader}></th>
          </tr>
        </thead>
        <tbody>
          {exports.map((row) => (
            <tr key={row.id}>
              <td className={`font-mono ${tableCell}`}>{row.filename}</td>
              <td className={`font-mono ${tableCell} text-[11px] uppercase`}>{row.fmt}</td>
              <td className={`${tableCell} tabular-nums`}>{formatBytes(row.size_bytes)}</td>
              <td className={`font-mono ${tableCell} text-[11px]`}>{formatWhen(row.created_at)}</td>
              <td className={tableCell}>
                <button className={buttonSecondary} onClick={() => download(row)}>
                  Download
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {exports.length === 0 && <p className={`mt-3 ${muted}`}>No exports yet.</p>}
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd dashboard && npx tsc --noEmit`
Expected: exit code 0

- [ ] **Step 3: Verify in the browser**

Run a query on `/ask`, export it as PDF, then open `/history`. Expected: the search appears with its scope and source count; clicking the question opens the stored result rendered through `ResultCard` with citation tabs, and it appears instantly (no model call); the export appears in the Exports table and Download re-downloads the same file; Delete removes the search row while the export row survives.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/pages/History.tsx
git commit -m "feat: history page for searches and exports"
```

---

### Task 13: Ingestion restyle and end-to-end verification

**Files:**
- Modify: `dashboard/src/pages/Status.tsx`
- Modify: `README.md`

**Interfaces:**
- Consumes: the `ui.ts` constants from Task 7.
- Produces: the finished feature.

- [ ] **Step 1: Restyle the Ingestion page**

In `dashboard/src/pages/Status.tsx`, keep all behavior and swap the presentation to the dense constants: use `pageTitle` for the heading, `tableHeader`/`tableCell` for the by-queue and by-mime tables, and tighten the step rows from `py-3` to `py-2`. Change the heading text from "Status" to "Ingestion" so it matches the sidebar label.

- [ ] **Step 2: Type-check and build**

Run: `cd dashboard && npx tsc --noEmit && npm run build`
Expected: both succeed.

- [ ] **Step 3: Rebuild the stack**

Run: `cd /d/dev/doc && docker compose up -d --build`
Expected: all containers start. Watch disk space while this runs — a full rebuild of `doc-worker` has filled the drive twice in this project's history.

- [ ] **Step 4: Verify the API end to end**

With `$TOKEN` set to the value of `BEARER_TOKEN` in `.env`:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/stats
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/documents
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/history/queries
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/history/exports
```

Expected: JSON from all four (not dashboard HTML — HTML means a route landed after the SPA catch-all). `/stats` document totals should match what `/folders` reports.

- [ ] **Step 5: Verify the full user flow in the browser**

At `http://localhost:8000`:
1. Overview shows real counts and recent activity.
2. Ask: run a query, confirm an answer with citations, export it as PDF.
3. History: the search is listed; opening it restores the result instantly; the export downloads.
4. Documents: the table lists files; filters narrow it.
5. Ingestion: all three steps still run.
6. Every page at a narrow window width, and in dark mode.

- [ ] **Step 6: Update the README**

Add a "Dashboard" section documenting the five sections and that query/export history is stored in Postgres and shared across everyone using the shared bearer token — the single-token model means history is not private per person, which someone deploying this to an office needs to know.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/pages/Status.tsx README.md
git commit -m "feat: restyle ingestion page and document the dashboard"
```

---

## Self-Review Notes

**Spec coverage:** `query_history` + `export_history` tables → Task 1. Serialization → Task 2. Server-side recording + `history_id` → Task 3. All five history endpoints → Task 4. `/stats` with the 10-event merged activity feed → Task 5. `/documents` → Task 6. Denser `ui.ts` + client → Task 7. Sidebar shell, `/` → Overview, `/status` → `/ingestion` redirect → Task 8. Ask scope bar + `ResultCard` extraction → Task 9. Overview page → Task 10. Documents page → Task 11. History page → Task 12. Ingestion restyle + end-to-end → Task 13. Out-of-scope items (per-user identity, retention policy, pinning) have no tasks, as intended.

**Type consistency:** `record_query` returns `int`, consumed as `history_id` in Task 3's responses, typed `history_id?: number` on `QueryResult` in Task 7, passed as the fourth argument to `downloadExport` in Task 9, and read as `payload.history_id` by the backend in Task 3. `ResultCardData` in Task 9 is structurally satisfied by both `QueryResult` (Task 7) and `QueryHistoryDetail` (Task 7), which is what lets Task 12 render stored results. `relative_to_raw` (Task 6) is exported under that exact name and used nowhere else. `build_overview` takes keyword-only arguments in both Task 5's test and its caller in `stats_query.py`.

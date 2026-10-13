"""The Progress page's SQL (progress_query.py) must say what the plain-Python builders in
progress.py say about the same rows. Runs against a live Postgres inside a transaction
that is rolled back; skipped when no database is reachable (it needs the pgvector image
from docker-compose.yml: POSTGRES_HOST=localhost to use the compose one)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.ingestion.progress import build_pipeline, build_progress, pipeline_candidates
from app.ingestion.progress_query import get_ingestion_progress
from app.models import ChunkRecord, FileRecord, JobRecord

RAW = "/zz_progress_test/raw"
NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """The page also reports Redis flags and the Docling server; neither is under test here."""
    import app.ingestion.progress_query as pq

    monkeypatch.setattr(pq, "is_paused", lambda: False)
    monkeypatch.setattr(pq.scan_status, "read", lambda: None)
    monkeypatch.setattr(pq, "docling_status", lambda: {})


@pytest.fixture()
def session():
    try:
        engine = create_engine(settings.database_url, connect_args={"connect_timeout": 2})
        connection = engine.connect()
        connection.execute(text("select 1"))
        connection.rollback()  # the ping started a transaction; the test begins its own
    except Exception:
        pytest.skip("no Postgres reachable")
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def _file(db, name, status, discovered_ago=60):
    f = FileRecord(
        path=f"{RAW}/{name}",
        sha256=f"h-{name}",
        mime_type="application/pdf",
        size_bytes=100,
        queue="convert_fast",
        status=status,
        discovered_at=NOW - timedelta(seconds=discovered_ago),
    )
    db.add(f)
    db.flush()
    return f


def _job(db, file, job_type, state, ago=30, error=None, retries=0):
    j = JobRecord(
        file_id=file.id,
        job_type=job_type,
        state=state,
        error=error,
        retries=retries,
        created_at=NOW - timedelta(seconds=ago + 5),
        updated_at=NOW - timedelta(seconds=ago),
    )
    db.add(j)
    db.flush()
    return j


def _chunk(db, file):
    db.add(ChunkRecord(file_id=file.id, text="x", heading="", chunk_index=0))
    db.flush()


def _build_corpus(db):
    # converted, indexed (index job done a moment ago -> "recently ready")
    a = _file(db, "a/ready.pdf", "converted")
    _job(db, a, "convert_fast", "done", ago=300)
    _job(db, a, "index", "done", ago=10)
    _chunk(db, a)
    # converted long ago, chunked, no index job row
    b = _file(db, "a/old.pdf", "summarized")
    _job(db, b, "convert_fast", "done", ago=9000)
    _job(db, b, "summarize", "done", ago=8000)
    _chunk(db, b)
    # converted, waiting for indexing
    c = _file(db, "a/indexing.pdf", "converted")
    _job(db, c, "convert_fast", "done", ago=120)
    # converted, indexing failed (stays out of the pipeline list)
    d = _file(db, "b/index_failed.pdf", "converted")
    _job(db, d, "convert_fast", "done", ago=200)
    _job(db, d, "index", "failed", ago=100, error="embedding server down", retries=3)
    # failed first, then retried and done: only the latest attempt counts
    e = _file(db, "b/retried.pdf", "converted")
    _job(db, e, "convert_fast", "failed", ago=500, error="old failure", retries=1)
    _job(db, e, "convert_fast", "done", ago=400)
    _job(db, e, "index", "done", ago=380)
    _chunk(db, e)
    # failed conversion
    f = _file(db, "b/broken.pdf", "failed")
    _job(db, f, "convert_fast", "failed", ago=50, error="boom", retries=3)
    # being converted now
    g = _file(db, "b/converting.pdf", "discovered", discovered_ago=400)
    _job(db, g, "convert_fast", "running", ago=70)
    # waiting in the queue, oldest first
    _file(db, "b/queued1.pdf", "discovered", discovered_ago=900)
    _file(db, "b/queued2.pdf", "discovered", discovered_ago=100)
    # running two job types at once, and a summary in flight
    h = _file(db, "a/two.pdf", "converted")
    _job(db, h, "convert_fast", "done", ago=60)
    _job(db, h, "summarize", "running", ago=20)
    _job(db, h, "index", "running", ago=15)
    # unsupported, in a folder, at the top level, and a chat attachment
    _file(db, "a/tool.exe", "unsupported")
    _file(db, "toplevel.pdf", "converted")
    u = _file(db, "uploads/chat.pdf", "converted")
    _job(db, u, "convert_fast", "done", ago=40)
    _job(db, u, "index", "done", ago=35)
    _chunk(db, u)


def _reference(db, raw_dir):
    """What the old implementation computed: every row into Python, then the pure builders."""
    file_rows = db.execute(
        select(FileRecord.id, FileRecord.path, FileRecord.status, FileRecord.size_bytes, FileRecord.discovered_at)
    ).all()
    files = [(r.id, r.path, r.status, r.size_bytes) for r in file_rows]
    latest_ids = select(func.max(JobRecord.id)).group_by(JobRecord.file_id, JobRecord.job_type)
    jobs = [
        (j.file_id, j.job_type, j.state, j.error, j.retries or 0, j.created_at, j.updated_at)
        for j in db.execute(select(JobRecord).where(JobRecord.id.in_(latest_ids))).scalars()
    ]
    now = datetime.now(timezone.utc)
    progress = build_progress(raw_dir, files, jobs, now)
    pipeline_files = [(r.id, r.path, r.status, r.size_bytes, r.discovered_at) for r in file_rows]
    candidates = pipeline_candidates(pipeline_files, jobs)
    chunked = (
        set(db.execute(select(ChunkRecord.file_id).where(ChunkRecord.file_id.in_(candidates)).distinct()).scalars())
        if candidates
        else set()
    )
    progress["pipeline"] = build_pipeline(raw_dir, pipeline_files, jobs, chunked, now, limit=1000)
    return progress


def _failure_keys(items):
    return sorted((f["file"], f["folder"], f["stage"], f["error"], f["retries"]) for f in items)


def test_the_sql_agrees_with_the_python_builders(session):
    _build_corpus(session)
    expected = _reference(session, RAW)
    got = get_ingestion_progress(session, RAW, pipeline_limit=1000)

    assert got["totals"] == expected["totals"]
    assert got["folders"] == expected["folders"]
    assert _failure_keys(got["failures"]) == _failure_keys(expected["failures"])
    assert got["failures_total"] == len(expected["failures"])
    assert sorted((a["file"], a["stage"]) for a in got["active"]) == sorted(
        (a["file"], a["stage"]) for a in expected["active"]
    )
    assert [(r["file"], r["stage"]) for r in got["recent"]] == [(r["file"], r["stage"]) for r in expected["recent"]]

    pipe, ref = got["pipeline"], expected["pipeline"]
    assert pipe["queue"] == ref["queue"]
    assert pipe["files_total"] == ref["files_total"]
    assert [(f["id"], f["stage"]) for f in pipe["files"]] == [(f["id"], f["stage"]) for f in ref["files"]]
    assert sorted(r["id"] for r in pipe["recently_ready"]) == sorted(r["id"] for r in ref["recently_ready"])


def test_what_the_corpus_means_in_plain_terms(session):
    _build_corpus(session)
    got = get_ingestion_progress(session, RAW, pipeline_limit=1000)

    stages = {f["file"]: f["stage"] for f in got["pipeline"]["files"]}
    assert stages["converting.pdf"] == "converting"
    assert stages["indexing.pdf"] == "indexing"
    assert stages["queued1.pdf"] == "queued" and stages["queued2.pdf"] == "queued"
    assert "index_failed.pdf" not in stages and "ready.pdf" not in stages
    # the retried file is not failing any more; the broken one and the failed index are
    assert {f["file"] for f in got["failures"]} == {"broken.pdf", "index_failed.pdf"}
    assert [f["file"] for f in got["pipeline"]["recently_ready"]][:1] == ["ready.pdf"]
    assert all(f["name"] != "uploads" for f in got["folders"])

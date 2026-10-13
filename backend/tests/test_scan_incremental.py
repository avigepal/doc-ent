"""run_scan writes in batches and reports progress, on a live Postgres inside a transaction
that is rolled back (skipped when no database is reachable)."""

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app import pipeline_runner, scan_status
from app.config import settings
from app.models import FileRecord


class FakeRedis:
    def __init__(self):
        self.data = {}

    def set(self, key, value, ex=None):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)


@pytest.fixture()
def session():
    try:
        engine = create_engine(settings.database_url, connect_args={"connect_timeout": 2})
        connection = engine.connect()
        connection.execute(text("select 1"))
        connection.rollback()
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


@pytest.fixture()
def raw_dir(tmp_path, monkeypatch):
    raw = tmp_path / "raw" / "docs"
    raw.mkdir(parents=True)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    redis = FakeRedis()
    monkeypatch.setattr(scan_status, "_client", lambda client=None: client or redis)
    # a scan only ever prunes records of files that vanished from raw/; real rows in a dev
    # database are not this test's business
    monkeypatch.setattr(pipeline_runner, "paths_to_prune", lambda known, seen: set())
    return raw


def _under(prefix):
    # startswith escapes the backslashes of a Windows path, which LIKE would read as escapes
    return FileRecord.path.startswith(str(prefix), autoescape=True)


def _paths(db, prefix):
    return set(db.execute(select(FileRecord.path).where(_under(prefix))).scalars())


def test_a_big_scan_is_saved_in_batches_and_reports_its_count(session, raw_dir, monkeypatch):
    for i in range(450):
        (raw_dir / f"note{i}.txt").write_text(f"note {i}")

    commits = []
    real_commit = session.commit

    def spying_commit():
        # how many of this test's files were saved by the time of each commit
        commits.append(session.scalar(select(func.count()).select_from(FileRecord).where(_under(raw_dir.parent))))
        real_commit()

    monkeypatch.setattr(session, "commit", spying_commit)

    result = pipeline_runner.run_scan(session)

    assert result["total_files"] == 450
    # saved as it went: 200 + 200 + 50, then the final commit -- not one commit at the very end
    assert commits[:3] == [200, 400, 450] and len(commits) == 4
    assert len(_paths(session, str(raw_dir.parent))) == 450

    status = scan_status.read()
    assert status["active"] is False and status["files_seen"] == 450


def test_new_files_start_out_discovered_and_unsupported_ones_do_not(session, raw_dir):
    (raw_dir / "a.txt").write_text("hello")
    (raw_dir / "tool.exe").write_bytes(b"MZ\x90\x00" + b"\x00" * 200)

    pipeline_runner.run_scan(session)

    statuses = dict(session.execute(select(FileRecord.path, FileRecord.status).where(_under(raw_dir.parent))).all())
    assert statuses[str(raw_dir / "a.txt")] == "discovered"
    assert statuses[str(raw_dir / "tool.exe")] == "unsupported"


def test_a_rescan_reuses_what_it_already_knows(session, raw_dir):
    (raw_dir / "a.txt").write_text("hello")
    first = pipeline_runner.run_scan(session)
    second = pipeline_runner.run_scan(session)

    assert first["total_files"] == second["total_files"] == 1
    assert second["changed"] == 0

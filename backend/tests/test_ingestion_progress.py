from datetime import datetime, timedelta, timezone

from app.ingestion.progress import build_progress

RAW = "/data/pipeline/raw"
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def _file(i, folder, name, status="discovered", size=100):
    return (i, f"{RAW}/{folder}/{name}", status, size)


def _job(file_id, job_type, state, secs_ago=0, error=None, retries=0):
    t = NOW - timedelta(seconds=secs_ago)
    return (file_id, job_type, state, error, retries, t, t)


def test_totals_count_summarized_files_as_converted_too():
    files = [_file(1, "a", "x.pdf", "summarized"), _file(2, "a", "y.pdf", "converted"), _file(3, "a", "z.pdf")]

    result = build_progress(RAW, files, [], NOW)

    assert result["totals"] == {
        "files": 3, "unsupported": 0, "discovered": 1, "converted": 2, "summarized": 1, "running": 0, "failed": 0,
    }


def test_uploads_folder_is_not_listed_but_still_counted_in_totals():
    files = [_file(1, "a", "x.pdf", "converted"), _file(2, "uploads", "chat.pdf", "converted")]

    result = build_progress(RAW, files, [], NOW)

    assert [f["name"] for f in result["folders"]] == ["a"]
    assert result["totals"]["files"] == 2


def test_running_job_shows_elapsed_time_and_stage():
    files = [_file(1, "a", "big.pdf")]
    jobs = [_job(1, "convert_fast", "running", secs_ago=95)]

    result = build_progress(RAW, files, jobs, NOW)

    assert result["active"] == [
        {"file": "big.pdf", "folder": "a", "stage": "Converting", "size_bytes": 100, "elapsed_seconds": 95}
    ]
    assert result["totals"]["running"] == 1
    assert result["folders"][0]["running"] == 1


def test_failure_carries_its_error_message():
    files = [_file(1, "a", "locked.pdf")]
    jobs = [_job(1, "convert_fast", "failed", error="Input document is not valid.", retries=2)]

    result = build_progress(RAW, files, jobs, NOW)

    assert result["failures"][0]["error"] == "Input document is not valid."
    assert result["failures"][0]["retries"] == 2
    assert result["totals"]["failed"] == 1


def test_recent_completions_are_newest_first_and_capped():
    files = [_file(i, "a", f"{i}.pdf") for i in range(1, 15)]
    jobs = [_job(i, "convert_fast", "done", secs_ago=i) for i in range(1, 15)]

    recent = build_progress(RAW, files, jobs, NOW)["recent"]

    assert len(recent) == 10
    assert recent[0]["file"] == "1.pdf"


def test_files_directly_in_raw_group_under_root():
    files = [(1, f"{RAW}/loose.pdf", "discovered", 5)]

    assert build_progress(RAW, files, [], NOW)["folders"][0]["name"] == "(root)"


def test_jobs_for_unknown_files_are_ignored():
    jobs = [_job(999, "summarize", "running")]

    result = build_progress(RAW, [], jobs, NOW)

    assert result["active"] == []
    assert result["totals"]["running"] == 0


def test_unsupported_files_do_not_count_toward_files_or_folder_totals():
    files = [_file(1, "a", "x.pdf", "summarized"), _file(2, "a", "setup.exe", "unsupported")]

    result = build_progress(RAW, files, [], NOW)

    assert result["totals"]["files"] == 1
    assert result["totals"]["unsupported"] == 1
    assert result["folders"][0]["total"] == 1


# ---------- per-file pipeline ----------

from app.ingestion.progress import build_pipeline, pipeline_candidates  # noqa: E402


def _pfile(i, folder, name, status="discovered", size=100, discovered_secs_ago=600):
    return (i, f"{RAW}/{folder}/{name}", status, size, NOW - timedelta(seconds=discovered_secs_ago))


def test_pipeline_places_each_file_in_its_stage_most_advanced_first():
    files = [
        _pfile(1, "a", "waiting.pdf"),
        _pfile(2, "a", "busy.pdf"),
        _pfile(3, "a", "converted.pdf", "converted"),
        _pfile(4, "a", "indexing.pdf", "converted"),
    ]
    jobs = [
        _job(2, "convert_fast", "running", secs_ago=40),
        _job(3, "convert_fast", "done", secs_ago=300),
        _job(3, "index", "done", secs_ago=290),
        _job(4, "convert_fast", "done", secs_ago=120),
        _job(4, "index", "running", secs_ago=15),
    ]

    result = build_pipeline(RAW, files, jobs, set(), NOW)

    assert result["queue"] == {"queued": 1, "converting": 1, "indexing": 1}
    assert [(f["file"], f["stage"]) for f in result["files"]] == [
        ("busy.pdf", "converting"),
        ("indexing.pdf", "indexing"),
        ("waiting.pdf", "queued"),
    ]
    assert result["files"][0]["elapsed_seconds"] == 40  # since the running conversion started
    assert result["files"][1]["elapsed_seconds"] == 15  # since indexing started
    assert result["files"][2]["elapsed_seconds"] == 600  # waiting since it was discovered


def test_pipeline_skips_failed_unsupported_and_finished_files():
    files = [
        _pfile(1, "a", "bad.pdf", "failed"),
        _pfile(2, "a", "tool.exe", "unsupported"),
        _pfile(3, "a", "done.pdf", "summarized"),
        _pfile(4, "a", "idx-failed.pdf", "converted"),
    ]
    jobs = [_job(3, "index", "done", secs_ago=9999), _job(4, "index", "failed", error="embedding server down")]

    result = build_pipeline(RAW, files, jobs, set(), NOW)

    assert result["files"] == []
    assert result["queue"] == {"queued": 0, "converting": 0, "indexing": 0}


def test_converted_file_with_chunks_but_no_index_job_is_ready_not_indexing():
    files = [_pfile(1, "a", "legacy.pdf", "converted")]

    assert build_pipeline(RAW, files, [], {1}, NOW)["queue"]["indexing"] == 0
    assert build_pipeline(RAW, files, [], set(), NOW)["queue"]["indexing"] == 1


def test_files_that_finished_indexing_moments_ago_are_reported_once_for_a_toast():
    files = [_pfile(1, "a", "new.pdf", "converted"), _pfile(2, "a", "old.pdf", "converted")]
    jobs = [_job(1, "index", "done", secs_ago=20), _job(2, "index", "done", secs_ago=3600)]

    result = build_pipeline(RAW, files, jobs, set(), NOW)

    assert [r["file"] for r in result["recently_ready"]] == ["new.pdf"]
    assert result["recently_ready"][0]["id"] == 1


def test_only_files_the_jobs_cannot_settle_are_looked_up_for_chunks():
    files = [
        _pfile(1, "a", "finished.pdf", "converted"),
        _pfile(2, "a", "unknown.pdf", "converted"),
        _pfile(3, "a", "queued.pdf"),
    ]
    jobs = [_job(1, "index", "done")]

    assert pipeline_candidates(files, jobs) == [2]


def test_pipeline_lists_at_most_the_limit_but_counts_everything(monkeypatch):
    monkeypatch.setattr("app.ingestion.progress.PIPELINE_LIMIT", 2)
    files = [_pfile(i, "a", f"f{i}.pdf") for i in range(1, 6)]

    result = build_pipeline(RAW, files, [], set(), NOW)

    assert len(result["files"]) == 2
    assert result["files_total"] == 5
    assert result["queue"]["queued"] == 5

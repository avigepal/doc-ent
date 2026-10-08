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

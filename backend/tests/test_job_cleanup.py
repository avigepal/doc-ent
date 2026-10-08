from app.job_cleanup import job_types_for_queues


def test_each_worker_queue_maps_to_the_job_types_it_runs():
    assert job_types_for_queues(["summarize"]) == ["summarize"]
    assert job_types_for_queues(["correlate"]) == ["index"]
    assert "convert_vision" in job_types_for_queues(["convert_fast"])


def test_unrelated_queues_close_nothing():
    assert job_types_for_queues(["auto_ingest", "reduce"]) == []


def test_multiple_queues_combine_without_duplicates():
    types = job_types_for_queues(["summarize", "correlate", "summarize"])
    assert types == ["summarize", "index"]

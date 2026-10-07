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

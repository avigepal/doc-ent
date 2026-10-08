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
        chat_only=False,
        conversation_id="abc-123",
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

"""The /query/stream endpoint's forced "report" mode (the dashboard's Full report
toggle). The database and the model are stubbed; what is checked is which path
the request takes."""

import json
import sys
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings
from app.db import get_session
from app.search import report
from app.search.report import ReportDocument


class FakeLLM:
    def __init__(self):
        self.calls = []

    def chat(self, system, user, temperature=0.2, **kwargs):
        self.calls.append(("chat", system))
        return "- a fact about the person" if system == report.MAP_SYSTEM_PROMPT else "NONE"

    def chat_stream(self, system, user, temperature=0.2):
        self.calls.append(("stream", system))
        yield "The full "
        yield "report."


@pytest.fixture
def client(monkeypatch):
    # The endpoint imports the file-editing module, which needs PyMuPDF. It is in the
    # Docker image; where it isn't installed (a bare local venv) a stub is enough here.
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, "pymupdf", MagicMock())

    import app.history.store as store
    import app.tasks.correlate as correlate

    llm = FakeLLM()
    monkeypatch.setattr(correlate, "_text_llm", llm)
    monkeypatch.setattr(store, "record_query", MagicMock(return_value=42))
    monkeypatch.setattr(store, "link_exports", MagicMock())
    monkeypatch.setattr(main, "_attachment_names", lambda session, ids: [])
    # a shortcut that must NOT be taken in report mode
    monkeypatch.setattr(main, "_catalog_answer", MagicMock(side_effect=AssertionError("catalog path taken")))
    main.app.dependency_overrides[get_session] = lambda: MagicMock()
    test_client = TestClient(main.app)
    test_client.llm = llm
    test_client.record_query = store.record_query
    yield test_client
    main.app.dependency_overrides.clear()


def _post(client, **payload):
    response = client.post(
        "/query/stream", json=payload, headers={"Authorization": f"Bearer {settings.bearer_token}"}
    )
    assert response.status_code == 200
    events = []
    for block in response.text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


def _docs(*names):
    return [ReportDocument(f"/data/raw/people/{n}", [("", f"text of {n}")]) for n in names]


def test_forced_report_reads_the_documents_in_scope_whatever_the_message_says(client, monkeypatch):
    seen = {}

    def fake_load(session, raw_dir, folders, author, title, file_ids):
        seen["scope"] = (folders, file_ids)
        return _docs("cv.pdf", "letter.pdf")

    monkeypatch.setattr(report, "load_report_documents", fake_load)

    # "list the files" would normally be answered from the library catalog
    events = _post(client, question="list the files", mode="report", folders=["people"])

    names = [n for n, _ in events]
    route = dict(events)["route"]
    assert route["action"] == "report" and route["source"] == "user"
    assert seen["scope"] == (["people"], None)
    assert names.count("status") == 3  # two documents read, then "Writing the report…"
    assert "".join(d["text"] for n, d in events if n == "token") == "The full report."
    assert names[-1] == "done"
    assert client.record_query.call_args.kwargs["route"] == "report"


def test_forced_report_uses_attached_files_when_there_are_any(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        report, "load_report_documents", lambda s, r, folders, a, t, file_ids: seen.update(ids=file_ids) or _docs("a.pdf")
    )

    _post(client, question="everything about this person", mode="report", file_ids=[7, 8])

    assert seen["ids"] == [7, 8]


def test_forced_report_with_no_documents_says_so_instead_of_chatting(client, monkeypatch):
    monkeypatch.setattr(report, "load_report_documents", MagicMock(side_effect=AssertionError("must not load")))

    events = _post(client, question="a full report", mode="report", chat_only=True)

    text = "".join(d["text"] for n, d in events if n == "token")
    assert text == report.NO_DOCUMENTS_MESSAGE
    assert client.llm.calls == []  # the model was not asked to chat


class _Store:
    """Stands in for the saved-notes table."""

    def __init__(self, *args, **kwargs):
        pass

    saved = {}

    def get(self, file_id, key, content_hash):
        return self.saved.get((file_id, key, content_hash))

    def put(self, file_id, key, content_hash, notes):
        self.saved[(file_id, key, content_hash)] = notes


def test_a_change_to_the_report_just_written_reuses_its_notes(client, monkeypatch):
    import app.history.store as store
    import app.search.report_notes as report_notes

    _Store.saved = {}
    monkeypatch.setattr(report_notes, "DbNotesStore", _Store)
    docs = [ReportDocument("/data/raw/people/cv.pdf", [("", "Asha")], 1)]
    monkeypatch.setattr(report, "load_report_documents", lambda *a, **k: docs)

    # first: the report itself reads the document and saves the notes
    first = _post(client, question="full report on this person", mode="report", conversation_id="c1")
    assert dict(first)["route"]["action"] == "report"
    reads_before = sum(1 for kind, system in client.llm.calls if kind == "chat" and system == report.MAP_SYSTEM_PROMPT)
    assert reads_before == 1

    # then: "make it shorter" in the same chat is a follow-up to that report
    monkeypatch.setattr(store, "report_context", lambda session, conversation_id, before_id=None: ("full report on this person", "old report"))
    second = _post(client, question="make it shorter", conversation_id="c1")

    route = dict(second)["route"]
    assert route["action"] == "report_refine" and route["label"] == "Updating the report…"
    reads_after = sum(1 for kind, system in client.llm.calls if kind == "chat" and system == report.MAP_SYSTEM_PROMPT)
    assert reads_after == reads_before  # nothing was read again
    assert ("stream", report.REFINE_SYSTEM_PROMPT) in client.llm.calls
    assert "".join(d["text"] for n, d in second if n == "token") == "The full report."
    assert client.record_query.call_args.kwargs["route"] == "report_refine"


def test_the_toggle_always_wins_over_a_follow_up_phrase(client, monkeypatch):
    import app.history.store as store

    monkeypatch.setattr(store, "report_context", lambda *a, **k: ("earlier request", "old report"))
    monkeypatch.setattr(report, "load_report_documents", lambda *a, **k: _docs("cv.pdf"))

    events = _post(client, question="make it shorter", mode="report", conversation_id="c1")

    assert dict(events)["route"]["action"] == "report"


def test_mode_is_validated(client):
    response = client.post(
        "/query/stream",
        json={"question": "x", "mode": "shout"},
        headers={"Authorization": f"Bearer {settings.bearer_token}"},
    )

    assert response.status_code == 422

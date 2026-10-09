from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings
from app.db import get_session
from app.search.suggest import (
    SUGGEST_COUNT,
    SUGGEST_SYSTEM_PROMPT,
    build_prompt,
    parse_suggestions,
    suggest_followups,
)


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, system, user, temperature=0.2, **kwargs):
        self.calls.append((system, user, kwargs))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


# ---------- reading the model's reply ----------

def test_a_json_array_is_read_as_is():
    raw = '["What was the total price?", "Who signed the quote?", "When does the warranty end?"]'

    assert parse_suggestions(raw) == ["What was the total price?", "Who signed the quote?", "When does the warranty end?"]


def test_json_inside_a_code_fence_or_with_chatter_around_it_is_found():
    raw = 'Sure! Here you go:\n```json\n["How long is the warranty?", "What does the PC include?"]\n```'

    assert parse_suggestions(raw) == ["How long is the warranty?", "What does the PC include?"]


def test_a_numbered_or_bulleted_list_works_when_it_is_not_json():
    raw = "1. How long is the warranty?\n2) What does the PC include?\n- Who is the vendor?\n\n* Is GST included?"

    assert parse_suggestions(raw) == [
        "How long is the warranty?",
        "What does the PC include?",
        "Who is the vendor?",
        "Is GST included?",
    ]


def test_the_original_question_duplicates_and_unusable_lines_are_dropped():
    raw = '["What is the total price?", "what is the total price", "Who is the vendor?", "who is the vendor??", "ok", "' + "x" * 300 + '", 7]'

    assert parse_suggestions(raw, original_question="What is the total price?") == ["Who is the vendor?"]


def test_at_most_five_are_kept():
    raw = str([f"Question number {i} here?" for i in range(12)]).replace("'", '"')

    assert len(parse_suggestions(raw)) == SUGGEST_COUNT == 5


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "I cannot help with that.", "Sorry, there is nothing to suggest.\nThe answer is complete.", "[]", "[1, 2, 3]"],
)
def test_nothing_usable_gives_no_suggestions(raw):
    assert parse_suggestions(raw) == []


# ---------- asking the model ----------

def test_the_model_sees_the_question_the_answer_and_where_it_came_from():
    llm = FakeLLM('["What about the warranty?"]')

    out = suggest_followups(
        "compare mac and pc",
        "The Mac Studio is cheaper [1].",
        ["/data/pipeline/raw/finance/PC_vs_Mac.pdf", "C:\\raw\\quote.pdf"],
        llm,
    )

    assert out == ["What about the warranty?"]
    system, user, kwargs = llm.calls[0]
    assert system == SUGGEST_SYSTEM_PROMPT
    assert "Original question: compare mac and pc" in user
    assert "The Mac Studio is cheaper [1]." in user
    assert "PC_vs_Mac.pdf, quote.pdf" in user  # names, not full paths
    assert kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}  # no thinking phase


def test_a_very_long_answer_is_cut_before_it_is_sent():
    prompt = build_prompt("q", "word " * 5000, [])

    assert len(prompt) < 7000


# ---------- the endpoint ----------

@pytest.fixture
def client(monkeypatch):
    import app.history.store as store
    import app.tasks.correlate as correlate

    llm = FakeLLM('["What is the warranty?", "Who is the vendor?", "Is GST included?"]')
    monkeypatch.setattr(correlate, "_text_llm", llm)
    monkeypatch.setattr(store, "set_suggestions", MagicMock())
    main.app.dependency_overrides[get_session] = lambda: MagicMock()
    test_client = TestClient(main.app)
    test_client.llm = llm
    test_client.set_suggestions = store.set_suggestions
    yield test_client
    main.app.dependency_overrides.clear()


def _post(client, **payload):
    return client.post("/query/suggest", json=payload, headers={"Authorization": f"Bearer {settings.bearer_token}"})


def test_the_endpoint_returns_the_questions_and_saves_them_with_the_reply(client):
    response = _post(client, question="compare", answer="The Mac is cheaper [1].", sources=["/raw/a.pdf"], history_id=42)

    assert response.status_code == 200
    assert response.json() == {"suggestions": ["What is the warranty?", "Who is the vendor?", "Is GST included?"]}
    client.set_suggestions.assert_called_once()
    assert client.set_suggestions.call_args.args[1:] == (42, ["What is the warranty?", "Who is the vendor?", "Is GST included?"])


def test_without_a_history_id_nothing_is_saved(client):
    response = _post(client, question="compare", answer="An answer.")

    assert response.json()["suggestions"]
    client.set_suggestions.assert_not_called()


def test_a_model_failure_is_an_empty_list_not_an_error(client):
    client.llm.reply = RuntimeError("llama-server unreachable")

    response = _post(client, question="q", answer="An answer.", history_id=1)

    assert response.status_code == 200
    assert response.json() == {"suggestions": []}
    client.set_suggestions.assert_not_called()


def test_an_empty_answer_never_calls_the_model(client):
    response = _post(client, question="q", answer="   ")

    assert response.json() == {"suggestions": []}
    assert client.llm.calls == []


def test_the_endpoint_needs_the_token(client):
    response = client.post("/query/suggest", json={"question": "q", "answer": "a"})

    assert response.status_code == 401

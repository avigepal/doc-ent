"""Chat follows the loaded text model: the active model is the loaded one (not only the .env setting),
it is cached briefly, and a client given a function asks it on every request."""

import json

import httpx
import pytest

from app import llm_models
from app.config import settings
from app.summarization.llm_client import LlamaClient


@pytest.fixture(autouse=True)
def fresh_cache():
    llm_models.forget_active_text_model()
    yield
    llm_models.forget_active_text_model()


def _state(monkeypatch, models: list[dict]):
    monkeypatch.setattr(llm_models, "list_text_models", lambda client=None: models)


def test_active_model_is_the_loaded_one(monkeypatch):
    _state(monkeypatch, [{"id": "A", "loaded": False}, {"id": "B", "loaded": True}])
    assert llm_models.active_text_model() == "B"


def test_falls_back_to_the_setting_when_nothing_is_loaded(monkeypatch):
    _state(monkeypatch, [{"id": "A", "loaded": False}])
    assert llm_models.active_text_model() == settings.llama_text_model


def test_falls_back_to_the_setting_when_the_server_is_unreachable(monkeypatch):
    def down(client=None):
        raise llm_models.ModelServerError("down")

    monkeypatch.setattr(llm_models, "list_text_models", down)
    assert llm_models.active_text_model() == settings.llama_text_model


def test_a_switch_is_seen_once_the_cache_is_cleared(monkeypatch):
    state = [{"id": "A", "loaded": True}, {"id": "B", "loaded": False}]
    _state(monkeypatch, state)
    assert llm_models.active_text_model() == "A"

    state[0]["loaded"], state[1]["loaded"] = False, True
    assert llm_models.active_text_model() == "A"  # still within the cache window
    llm_models.forget_active_text_model()  # what load and unload do
    assert llm_models.active_text_model() == "B"


def test_client_asks_for_the_model_on_each_request():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    current = {"name": "first"}
    client = LlamaClient(
        base_url="http://x",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        model=lambda: current["name"],
    )
    client.chat(system="s", user="u")
    current["name"] = "second"
    client.chat(system="s", user="u")
    assert seen == ["first", "second"]


def test_client_with_a_plain_string_still_sends_that_model():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = LlamaClient(
        base_url="http://x",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        model="fixed",
    )
    client.chat(system="s", user="u")
    assert seen == ["fixed"]

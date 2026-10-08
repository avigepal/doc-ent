import json

import httpx
import pytest

from app.summarization.llm_client import LlamaClient


def _client_with_transport(handler) -> LlamaClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    return LlamaClient(base_url="http://llama-text:8080", http_client=http_client, model="qwen3-30b")


def test_chat_posts_openai_shaped_request_and_parses_response():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "a tight summary"}}]},
        )

    client = _client_with_transport(handler)
    result = client.chat(system="You summarize text.", user="long document text")

    assert captured["url"] == "http://llama-text:8080/v1/chat/completions"
    assert captured["body"]["model"] == "qwen3-30b"
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "You summarize text."},
        {"role": "user", "content": "long document text"},
    ]
    assert result == "a tight summary"


def test_chat_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server exploded"})

    client = _client_with_transport(handler)

    with pytest.raises(httpx.HTTPStatusError):
        client.chat(system="sys", user="user")


def _sse(*deltas: str) -> bytes:
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': d}}]})}" for d in deltas]
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode()


def test_chat_stream_yields_each_delta_in_order():
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content=_sse("Hel", "lo", " world"))

    client = _client_with_transport(handler)

    pieces = list(client.chat_stream(system="sys", user="hi"))

    assert pieces == ["Hel", "lo", " world"]


def test_chat_stream_stops_at_done_and_skips_empty_deltas():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse("a", "", "b"))

    client = _client_with_transport(handler)

    assert list(client.chat_stream(system="sys", user="hi")) == ["a", "b"]

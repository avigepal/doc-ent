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

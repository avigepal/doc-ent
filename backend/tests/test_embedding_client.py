import json

import httpx

from app.search.embedding_client import EmbeddingClient


def _client_with_transport(handler) -> EmbeddingClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    return EmbeddingClient(base_url="http://llama-embed:8080", http_client=http_client, model="bge-m3")


def test_embed_posts_openai_shaped_request_and_returns_vectors_in_order():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.2, 0.3]},
                    {"index": 0, "embedding": [0.1, 0.1]},
                ]
            },
        )

    client = _client_with_transport(handler)
    vectors = client.embed(["first", "second"])

    assert captured["body"] == {"model": "bge-m3", "input": ["first", "second"]}
    # returned in index order regardless of response order
    assert vectors == [[0.1, 0.1], [0.2, 0.3]]


def test_embed_empty_list_returns_empty_without_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"data": []})

    client = _client_with_transport(handler)
    assert client.embed([]) == []
    assert calls == []


def test_embed_truncates_vectors_wider_than_the_pgvector_column():
    # Qwen3-Embedding-4B natively outputs 2560 dims; ChunkRecord.embedding
    # is a fixed Vector(1024) column, so wider vectors must be truncated
    # to a 1024-dim MRL prefix and renormalized before storage.
    wide_vector = [1.0] * 2560

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": wide_vector}]})

    client = _client_with_transport(handler)
    [vector] = client.embed(["text"])

    assert len(vector) == 1024
    norm = sum(x * x for x in vector) ** 0.5
    assert abs(norm - 1.0) < 1e-9

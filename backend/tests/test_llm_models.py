"""The text-model list and the load/unload calls, against a fake llama-server (httpx.MockTransport).
No real server is needed."""

import json

import httpx
import pytest

from app import llm_models


def _entry(model_id: str, value: str, args: list[str] | None = None) -> dict:
    return {"id": model_id, "status": {"value": value, "args": args or ["/bin/llama-server", "--model", model_id]}}


class FakeServer:
    """Records the calls made to it and answers /models from a mutable list."""

    def __init__(self, entries: list[dict]):
        self.entries = entries
        self.calls: list[tuple[str, str, dict | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": self.entries})
        if request.url.path == "/models/unload":
            for e in self.entries:
                if e["id"] == body["model"]:
                    e["status"]["value"] = "unloaded"
            return httpx.Response(200, json={"success": True})
        if request.url.path == "/v1/chat/completions":
            for e in self.entries:
                if e["id"] == body["model"]:
                    e["status"]["value"] = "loaded"
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def test_list_leaves_out_embedding_and_reranker_models():
    server = FakeServer(
        [
            _entry("Qwen3.8-27B-MTP-Q4_K_M", "loaded"),
            _entry("Qwen3.6-35B-A3B-UD-Q4_K_XL", "unloaded"),
            _entry("Qwen3-Embedding-4B", "unloaded", ["x", "--embeddings"]),
            _entry("bge-reranker-v2-m3", "unloaded", ["x", "--reranking"]),
        ]
    )
    models = llm_models.list_text_models(server.client())
    assert models == [
        {"id": "Qwen3.8-27B-MTP-Q4_K_M", "loaded": True},
        {"id": "Qwen3.6-35B-A3B-UD-Q4_K_XL", "loaded": False},
    ]


def test_load_unloads_the_other_text_model_first():
    server = FakeServer(
        [
            _entry("Qwen3.8-27B-MTP-Q4_K_M", "loaded"),
            _entry("Qwen3.6-35B-A3B-UD-Q4_K_XL", "unloaded"),
            _entry("Qwen3-Embedding-4B", "loaded", ["x", "--embeddings"]),
        ]
    )
    llm_models.load_text_model("Qwen3.6-35B-A3B-UD-Q4_K_XL", server.client())

    unloads = [body["model"] for _, path, body in server.calls if path == "/models/unload"]
    assert unloads == ["Qwen3.8-27B-MTP-Q4_K_M"]  # the embedding model is never touched
    chats = [body["model"] for _, path, body in server.calls if path == "/v1/chat/completions"]
    assert chats == ["Qwen3.6-35B-A3B-UD-Q4_K_XL"]


def test_unload_posts_the_model_id():
    server = FakeServer([_entry("Qwen3.8-27B-MTP-Q4_K_M", "loaded")])
    llm_models.unload_text_model("Qwen3.8-27B-MTP-Q4_K_M", server.client())
    assert ("POST", "/models/unload", {"model": "Qwen3.8-27B-MTP-Q4_K_M"}) in server.calls


def test_embedding_models_cannot_be_loaded_or_unloaded_here():
    server = FakeServer([_entry("Qwen3-Embedding-4B", "loaded", ["x", "--embeddings"])])
    with pytest.raises(KeyError):
        llm_models.unload_text_model("Qwen3-Embedding-4B", server.client())
    with pytest.raises(KeyError):
        llm_models.load_text_model("Qwen3-Embedding-4B", server.client())
    assert not any(path.startswith("/models/") and method == "POST" for method, path, _ in server.calls)


def test_unreachable_server_is_a_model_server_error():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    with pytest.raises(llm_models.ModelServerError):
        llm_models.list_text_models(client)

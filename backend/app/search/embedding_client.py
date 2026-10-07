"""Client for llama-server's /v1/embeddings endpoint (--embedding mode),
served as its own process separate from the text/vision models — see
docker-compose.yml's commented-out llama-embed service.
"""

from __future__ import annotations

import httpx


class EmbeddingClient:
    def __init__(self, base_url: str, http_client: httpx.Client | None = None, model: str = "local", timeout: float = 60.0):
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.Client(timeout=timeout)
        self._model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.post(
            f"{self._base_url}/v1/embeddings",
            json={"model": self._model, "input": texts},
        )
        response.raise_for_status()
        data = response.json()
        # OpenAI-shaped response: data["data"] is a list of {"index": i, "embedding": [...]}
        by_index = sorted(data["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in by_index]

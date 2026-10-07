"""Client for llama-server's /v1/embeddings endpoint (--embedding mode),
served as its own process separate from the text/vision models — see
docker-compose.yml's commented-out llama-embed service.
"""

from __future__ import annotations

import math

import httpx

# ChunkRecord.embedding is a fixed pgvector Vector(1024) column (see
# app/models.py). Qwen3-Embedding-4B natively outputs 2560 dims, but it's
# trained with Matryoshka Representation Learning specifically so a
# prefix of its output can be used at a smaller dimension without the
# usual truncation quality loss — officially supported sizes include
# 1024. If you swap in a model whose native output is already 1024
# (e.g. BGE-M3), this is a no-op.
_TARGET_DIM = 1024


def _truncate_and_renormalize(vector: list[float], dim: int = _TARGET_DIM) -> list[float]:
    if len(vector) <= dim:
        return vector
    truncated = vector[:dim]
    norm = math.sqrt(sum(x * x for x in truncated))
    if norm == 0:
        return truncated
    return [x / norm for x in truncated]


class EmbeddingClient:
    def __init__(
        self,
        base_url: str,
        http_client: httpx.Client | None = None,
        model: str = "local",
        timeout: float = 60.0,
        api_key: str | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.Client(timeout=timeout)
        self._model = model
        self._api_key = api_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
        response = self._client.post(
            f"{self._base_url}/v1/embeddings",
            json={"model": self._model, "input": texts},
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
        # OpenAI-shaped response: data["data"] is a list of {"index": i, "embedding": [...]}
        by_index = sorted(data["data"], key=lambda item: item["index"])
        return [_truncate_and_renormalize(item["embedding"]) for item in by_index]

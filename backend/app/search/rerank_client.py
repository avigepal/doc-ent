"""Reordering search results with a reranker model.

Search finds candidates by vector similarity and keywords; a reranker reads the question and each
candidate passage *together* and scores how well the passage answers it, which is more accurate than
comparing embeddings. It runs in llama-server (--reranking), which exposes /v1/rerank.

Only the ORDER (and which passages are kept) comes from the reranker. Each passage keeps the score it
already had, so the "nothing relevant found" threshold means what it always did.

Off unless LLAMA_RERANK_URL is set. If the reranker is slow, down or answers nonsense, search carries
on with its own order: reranking can never stop an answer.
"""

from __future__ import annotations

import logging
import time
from typing import Sequence

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# After a failure the reranker is not tried again for this long, so a server that is down costs
# one slow question, not every question.
RETRY_AFTER_FAILURE_S = 60.0
MAX_CANDIDATES = 100


class RerankClient:
    def __init__(
        self,
        base_url: str,
        http_client: httpx.Client | None = None,
        model: str = "local",
        timeout: float = 30.0,
        api_key: str | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.Client(timeout=httpx.Timeout(timeout, connect=3.0))
        self._model = model
        self._api_key = api_key
        self._down_until = 0.0

    def available(self, now: float | None = None) -> bool:
        return (time.monotonic() if now is None else now) >= self._down_until

    def scores(self, query: str, documents: Sequence[str]) -> list[float]:
        """One relevance score per document, in the order given. Raises when the reply cannot be used."""
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
        response = self._client.post(
            f"{self._base_url}/v1/rerank",
            json={"model": self._model, "query": query, "documents": list(documents)},
            headers=headers,
        )
        response.raise_for_status()
        results = response.json()["results"]
        scores: list[float | None] = [None] * len(documents)
        for item in results:
            index = int(item["index"])
            if 0 <= index < len(documents):
                scores[index] = float(item["relevance_score"])
        if any(score is None for score in scores):
            raise ValueError("the reranker did not score every passage")
        return scores  # type: ignore[return-value]

    def mark_failed(self, now: float | None = None) -> None:
        self._down_until = (time.monotonic() if now is None else now) + RETRY_AFTER_FAILURE_S


_reranker: RerankClient | None = None


def get_reranker() -> RerankClient | None:
    """The configured reranker, or None when LLAMA_RERANK_URL is empty."""
    global _reranker
    if not settings.llama_rerank_url:
        return None
    if _reranker is None:
        _reranker = RerankClient(
            base_url=settings.llama_rerank_url,
            model=settings.llama_rerank_model,
            api_key=settings.llama_rerank_api_key or None,
            timeout=settings.rerank_timeout_s,
        )
    return _reranker


def candidates_wanted(k: int, reranker: RerankClient | None) -> int:
    """How many passages search should hand to the reranker: more than the k that will be kept."""
    if reranker is None or not reranker.available():
        return k
    return min(MAX_CANDIDATES, max(k, settings.rerank_candidates, k * 2))


def passage_for_reranker(heading: str, text: str, limit: int | None = None) -> str:
    limit = settings.rerank_passage_chars if limit is None else limit
    body = f"{heading}\n{text}" if heading else text
    return body[:limit]


def rerank_keep_best(query: str, items: list, k: int, reranker: RerankClient | None, describe) -> list:
    """`items` best-first from search, `describe(item)` -> (heading, text). Returns the k best by the
    reranker, or the first k unchanged when there is no reranker, nothing to choose between, or it fails."""
    if reranker is None or not query.strip() or len(items) <= 1 or not reranker.available():
        return items[:k]
    documents = [passage_for_reranker(*describe(item)) for item in items]
    try:
        scores = reranker.scores(query, documents)
    except Exception:
        logger.warning("reranking failed; keeping the search order for a minute", exc_info=True)
        reranker.mark_failed()
        return items[:k]
    order = sorted(range(len(items)), key=lambda i: (-scores[i], i))  # ties keep the search order
    return [items[i] for i in order[:k]]

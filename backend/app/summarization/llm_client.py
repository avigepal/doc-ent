"""Thin client for llama.cpp's `llama-server`, which exposes an
OpenAI-compatible `/v1/chat/completions` endpoint. One instance per model
(text / vision / embeddings each run as their own llama-server process —
see docker-compose.yml's commented-out llama-* services).

An httpx.Client can be injected for testing (see tests/test_llm_client.py,
which uses httpx.MockTransport — no real network calls, no server needed).
"""

from __future__ import annotations

import httpx


class LlamaClient:
    def __init__(
        self,
        base_url: str,
        http_client: httpx.Client | None = None,
        model: str = "local",
        timeout: float = 120.0,
        api_key: str | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.Client(timeout=timeout)
        self._model = model
        self._api_key = api_key

    def chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
        response = self._client.post(
            f"{self._base_url}/v1/chat/completions",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
            },
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

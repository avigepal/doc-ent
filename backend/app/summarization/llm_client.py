"""Thin client for llama.cpp's `llama-server`, which exposes an
OpenAI-compatible `/v1/chat/completions` endpoint. One instance per model
(text / vision / embeddings each run as their own llama-server process —
see docker-compose.yml's commented-out llama-* services).

An httpx.Client can be injected for testing (see tests/test_llm_client.py,
which uses httpx.MockTransport — no real network calls, no server needed).
"""

from __future__ import annotations

import json
from typing import Iterator

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

    def chat_with_image(
        self,
        system: str,
        user_text: str,
        image_data_url: str,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> str:
        """One-shot vision request: the image goes in as an OpenAI-style
        `image_url` content part (a base64 data URL), which llama-server
        accepts when the model was started with an mmproj. max_tokens is
        capped so a model stuck repeating itself can't run unbounded."""
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
        response = self._client.post(
            f"{self._base_url}/v1/chat/completions",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {"type": "image_url", "image_url": {"url": image_data_url}},
                        ],
                    },
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            headers=headers,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"] or ""

    def chat_stream(self, system: str, user: str, temperature: float = 0.2) -> Iterator[str]:
        """Same request as chat(), but with stream=True — yields each
        token/delta as it arrives instead of waiting for the full
        response. OpenAI-compatible SSE: lines shaped "data: {...}",
        terminated by a literal "data: [DONE]" line."""
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else None
        with self._client.stream(
            "POST",
            f"{self._base_url}/v1/chat/completions",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": temperature,
                "stream": True,
            },
            headers=headers,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[len("data: ") :]
                if payload == "[DONE]":
                    break
                delta = json.loads(payload)["choices"][0]["delta"].get("content")
                if delta:
                    yield delta

"""Load and unload the text models that the llama-server router serves.

llama-server in router mode lists every model it knows (text, embedding, reranker) with a status
of "loaded" or "unloaded". The dashboard only manages the text models, so anything started with
--embeddings or --reranking is left out, both from the list and from the load/unload calls.

Loading a text model here unloads the other text models first, because the GPU usually cannot hold
two of them at once. Loading is done by sending a one-token chat request: the router loads the
model on demand, and that is the same path a normal question takes.
"""

from __future__ import annotations

import time

import httpx

from app.config import settings

# Arguments that mark a model as something other than a chat model.
_NON_TEXT_FLAGS = ("--embeddings", "--reranking")

# How long a looked-up active model is trusted before asking the server again. A short answer
# keeps chat from asking the server on every single request; load and unload clear it at once.
_ACTIVE_TTL_SECONDS = 5.0
_active: dict = {"name": None, "at": 0.0}


class ModelServerError(Exception):
    """The llama-server could not be reached or refused the request."""


def _base() -> str:
    return settings.llama_text_url.rstrip("/")


def _headers() -> dict[str, str]:
    key = settings.llama_text_api_key
    return {"Authorization": f"Bearer {key}"} if key else {}


def _get_models(client: httpx.Client) -> list[dict]:
    response = client.get(f"{_base()}/models", headers=_headers())
    response.raise_for_status()
    return response.json().get("data", [])


def _is_text_model(entry: dict) -> bool:
    args = (entry.get("status") or {}).get("args") or []
    return not any(flag in args for flag in _NON_TEXT_FLAGS)


def _is_loaded(entry: dict) -> bool:
    return (entry.get("status") or {}).get("value") == "loaded"


def list_text_models(client: httpx.Client | None = None) -> list[dict]:
    """The text models and whether each one is loaded now: [{"id": ..., "loaded": bool}]."""
    http = client or httpx.Client(timeout=15)
    try:
        entries = _get_models(http)
    except httpx.HTTPError as err:
        raise ModelServerError(f"model server unreachable: {err}") from err
    return [{"id": e["id"], "loaded": _is_loaded(e)} for e in entries if _is_text_model(e)]


def active_text_model() -> str:
    """The chat model chat should use: the text model that is loaded now. If none is loaded (or the
    model server cannot be reached), the LLAMA_TEXT_MODEL setting is used, as before."""
    now = time.monotonic()
    if _active["name"] and now - _active["at"] < _ACTIVE_TTL_SECONDS:
        return _active["name"]
    name = None
    try:
        name = next((m["id"] for m in list_text_models() if m["loaded"]), None)
    except ModelServerError:
        name = None
    name = name or settings.llama_text_model
    _active.update(name=name, at=now)
    return name


def forget_active_text_model() -> None:
    _active.update(name=None, at=0.0)


def _check_text_model(http: httpx.Client, model_id: str) -> None:
    known = {m["id"] for m in list_text_models(http)}
    if model_id not in known:
        raise KeyError(model_id)


def unload_text_model(model_id: str, client: httpx.Client | None = None) -> None:
    http = client or httpx.Client(timeout=60)
    try:
        _check_text_model(http, model_id)
        response = http.post(f"{_base()}/models/unload", json={"model": model_id}, headers=_headers())
        response.raise_for_status()
    except httpx.HTTPError as err:
        raise ModelServerError(f"could not unload {model_id}: {err}") from err
    finally:
        forget_active_text_model()


def load_text_model(model_id: str, client: httpx.Client | None = None) -> None:
    """Make model_id the only loaded text model. Other text models are unloaded first."""
    http = client or httpx.Client(timeout=600)
    try:
        _check_text_model(http, model_id)
        for entry in list_text_models(http):
            if entry["loaded"] and entry["id"] != model_id:
                http.post(f"{_base()}/models/unload", json={"model": entry["id"]}, headers=_headers()).raise_for_status()
        body = {
            "model": model_id,
            "messages": [{"role": "user", "content": "ok"}],
            "max_tokens": 1,
            "temperature": 0,
        }
        http.post(f"{_base()}/v1/chat/completions", json=body, headers=_headers()).raise_for_status()
    except httpx.HTTPError as err:
        raise ModelServerError(f"could not load {model_id}: {err}") from err
    finally:
        forget_active_text_model()

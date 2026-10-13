import json

import httpx
import pytest

from app.config import settings
from app.search import rerank_client
from app.search.rerank_client import RerankClient, candidates_wanted, rerank_keep_best


def _client(handler, **kwargs):
    return RerankClient("http://rerank:8080/", http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def _scoring(score_by_doc):
    """A fake /v1/rerank that scores each document by a lookup (results come back in score order, like llama-server)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        results = [{"index": i, "relevance_score": score_by_doc[d]} for i, d in enumerate(body["documents"])]
        results.sort(key=lambda r: -r["relevance_score"])
        return httpx.Response(200, json={"results": results})

    return handler


# ---------- the client ----------


def test_scores_come_back_in_the_order_the_documents_were_sent():
    client = _client(_scoring({"a": 0.1, "b": 0.9, "c": 0.5}), api_key="k", model="rr")

    assert client.scores("question", ["a", "b", "c"]) == [0.1, 0.9, 0.5]


def test_the_request_names_the_model_and_carries_the_key():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 1.0}]})

    _client(handler, api_key="secret", model="qwen3-rerank").scores("q", ["d"])

    assert seen["url"] == "http://rerank:8080/v1/rerank"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"] == {"model": "qwen3-rerank", "query": "q", "documents": ["d"]}


@pytest.mark.parametrize(
    "reply",
    [{"results": [{"index": 0, "relevance_score": 1}]}, {"results": []}, {"nothing": 1}],  # one of two scored, none, wrong shape
)
def test_a_reply_that_does_not_score_every_document_is_an_error(reply):
    client = _client(lambda request: httpx.Response(200, json=reply))

    with pytest.raises((ValueError, KeyError)):
        client.scores("q", ["a", "b"])


# ---------- keeping the best ----------


def test_the_best_k_by_the_reranker_are_kept_in_its_order():
    client = _client(_scoring({"a": 0.2, "b": 0.9, "c": 0.7, "d": 0.1}))
    items = [("", n) for n in "abcd"]

    kept = rerank_keep_best("q", items, 2, client, describe=lambda item: item)

    assert kept == [("", "b"), ("", "c")]


def test_equal_scores_keep_the_search_order():
    client = _client(_scoring({"x": 0.5, "y": 0.5, "z": 0.5}))
    items = [("", "x"), ("", "y"), ("", "z")]

    assert rerank_keep_best("q", items, 3, client, describe=lambda item: item) == items


def test_without_a_reranker_or_with_nothing_to_choose_between_the_search_order_stands():
    items = [("", n) for n in "abc"]

    assert rerank_keep_best("q", items, 2, None, describe=lambda item: item) == items[:2]
    assert rerank_keep_best("  ", items, 2, _client(_scoring({})), describe=lambda item: item) == items[:2]
    assert rerank_keep_best("q", items[:1], 2, _client(_scoring({})), describe=lambda item: item) == items[:1]


def test_a_failing_reranker_never_stops_search_and_is_left_alone_for_a_minute():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500)

    client = _client(handler)
    items = [("", n) for n in "abc"]

    assert rerank_keep_best("q", items, 2, client, describe=lambda item: item) == items[:2]
    assert rerank_keep_best("q", items, 2, client, describe=lambda item: item) == items[:2]
    assert len(calls) == 1  # the second question did not wait for a server that is down

    assert not client.available()
    assert client.available(now=10**9)  # a minute later it is tried again


def test_a_long_passage_is_cut_before_it_is_sent():
    seen = {}

    def handler(request):
        seen["documents"] = json.loads(request.content)["documents"]
        return httpx.Response(200, json={"results": [{"index": i, "relevance_score": 1.0 - i} for i in range(2)]})

    rerank_keep_best("q", [("Head", "x" * 10_000), ("", "short")], 1, _client(handler), describe=lambda item: item)

    assert len(seen["documents"][0]) == settings.rerank_passage_chars
    assert seen["documents"][0].startswith("Head\n")
    assert seen["documents"][1] == "short"


# ---------- how many passages search fetches ----------


def test_search_fetches_more_than_k_only_when_a_reranker_is_in_use(monkeypatch):
    monkeypatch.setattr(settings, "rerank_candidates", 40)
    client = _client(_scoring({}))

    assert candidates_wanted(12, None) == 12
    assert candidates_wanted(12, client) == 40
    assert candidates_wanted(36, client) == 72  # a detailed answer reads more, so it picks from more
    assert candidates_wanted(80, client) == 100  # capped

    client.mark_failed()
    assert candidates_wanted(12, client) == 12  # a reranker that is down costs nothing


def test_no_url_means_no_reranker(monkeypatch):
    monkeypatch.setattr(settings, "llama_rerank_url", "")
    monkeypatch.setattr(rerank_client, "_reranker", None)

    assert rerank_client.get_reranker() is None


def test_a_url_builds_one_shared_reranker(monkeypatch):
    monkeypatch.setattr(settings, "llama_rerank_url", "http://rerank:8080")
    monkeypatch.setattr(rerank_client, "_reranker", None)

    first = rerank_client.get_reranker()

    assert first is not None and rerank_client.get_reranker() is first
    monkeypatch.setattr(rerank_client, "_reranker", None)  # do not leak it into other tests

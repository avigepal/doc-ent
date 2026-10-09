from app.search.query import run_chat, run_query, stream_query
from app.search.retrieval import RetrievedChunk


class FakeLLM:
    def __init__(self, response: str = "answer", stream_pieces: list[str] | None = None):
        self.calls: list[tuple[str, str]] = []
        self.response = response
        self.stream_pieces = stream_pieces if stream_pieces is not None else list(response)

    def chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        self.calls.append((system, user))
        return self.response

    def chat_stream(self, system: str, user: str, temperature: float = 0.2):
        self.calls.append((system, user))
        yield from self.stream_pieces


def _chunk(file_path, text, score=0.9):
    return RetrievedChunk(file_path=file_path, heading="H", text=text, score=score)


def test_run_query_combines_grounded_answer_with_no_correlation_for_single_source():
    llm = FakeLLM("the answer")
    chunks = [_chunk("a.pdf", "relevant text")]

    result = run_query("what is x?", chunks, {}, llm)

    assert result.answer == "the answer"
    assert result.grounded is True
    assert result.sources == ["a.pdf"]
    assert result.cross_doc is None
    assert result.statistical is None


def test_run_query_includes_cross_doc_when_multiple_sources_retrieved():
    llm = FakeLLM("combined")
    chunks = [_chunk("a.pdf", "text a"), _chunk("b.pdf", "text b")]

    result = run_query("how do these relate?", chunks, {}, llm)

    assert result.cross_doc is not None
    assert result.cross_doc.sources == ["a.pdf", "b.pdf"]


def test_run_query_not_grounded_when_nothing_clears_threshold():
    llm = FakeLLM()
    chunks = [_chunk("a.pdf", "irrelevant", score=0.05)]

    result = run_query("what is x?", chunks, {}, llm, similarity_threshold=0.3)

    assert result.grounded is False
    assert result.sources == []


def test_run_chat_skips_retrieval_and_talks_to_model_directly():
    llm = FakeLLM("hi there")

    result = run_chat("hello", llm)

    assert result.answer == "hi there"
    assert result.grounded is False
    assert result.sources == []
    assert result.cross_doc is None
    assert result.statistical is None
    # no corpus context in the prompt -- just the question itself
    assert llm.calls == [(llm.calls[0][0], "hello")]


def test_stream_query_chat_only_yields_meta_then_tokens_then_extra():
    llm = FakeLLM(stream_pieces=["hi", " there"])

    events = list(stream_query("hello", [], {}, llm, chat_only=True))

    assert events[0] == {"event": "meta", "data": {"sources": [], "grounded": False}}
    assert events[1] == {"event": "token", "data": {"text": "hi"}}
    assert events[2] == {"event": "token", "data": {"text": " there"}}
    assert events[3] == {"event": "extra", "data": {"cross_doc": None, "statistical": None}}


def test_stream_query_streams_the_answer_and_leaves_cross_doc_to_the_compare_button():
    llm = FakeLLM(stream_pieces=["the ", "answer"])
    chunks = [_chunk("a.pdf", "text a"), _chunk("b.pdf", "text b")]

    # a comparison-style question over two files used to trigger a second model call
    events = list(stream_query("how do these relate?", chunks, {}, llm, chat_only=False))

    assert events[0] == {"event": "meta", "data": {"sources": ["a.pdf", "b.pdf"], "grounded": True}}
    tokens = [e["data"]["text"] for e in events if e["event"] == "token"]
    assert tokens == ["the ", "answer"]
    extra = events[-1]
    assert extra["event"] == "extra"
    assert extra["data"]["cross_doc"] is None
    assert len(llm.calls) == 1  # the answer only; no cross-document call


def test_stream_query_not_grounded_skips_streaming_and_correlation():
    llm = FakeLLM()
    chunks = [_chunk("a.pdf", "irrelevant", score=0.05)]

    events = list(stream_query("what is x?", chunks, {}, llm, chat_only=False, similarity_threshold=0.3))

    assert events[0] == {"event": "meta", "data": {"sources": [], "grounded": False}}
    assert events[1]["event"] == "token"
    assert "couldn't find" in events[1]["data"]["text"]
    assert events[2] == {"event": "extra", "data": {"cross_doc": None, "statistical": None}}
    assert llm.calls == []  # never called chat/chat_stream -- threshold check short-circuits first


def test_run_query_skips_cross_doc_on_an_ordinary_question_even_with_multiple_sources():
    llm = FakeLLM("answer")
    chunks = [_chunk("a.pdf", "text a"), _chunk("b.pdf", "text b")]

    result = run_query("how do I enable wake-on-lan?", chunks, {}, llm)

    assert result.cross_doc is None
    assert len(llm.calls) == 1  # only the grounded answer, no second model call


def test_stream_query_never_ends_with_an_empty_answer():
    from app.search.query import EMPTY_ANSWER_MESSAGE

    llm = FakeLLM(stream_pieces=[])  # the model streams back no text at all
    chunks = [_chunk("a.pdf", "text a")]

    events = list(stream_query("what is x?", chunks, {}, llm))

    tokens = [e["data"]["text"] for e in events if e["event"] == "token"]
    assert tokens == [EMPTY_ANSWER_MESSAGE]


def test_stream_query_chat_only_also_falls_back_when_the_model_is_silent():
    from app.search.query import EMPTY_ANSWER_MESSAGE

    llm = FakeLLM(stream_pieces=[])

    events = list(stream_query("hello", [], {}, llm, chat_only=True))

    assert [e["data"]["text"] for e in events if e["event"] == "token"] == [EMPTY_ANSWER_MESSAGE]

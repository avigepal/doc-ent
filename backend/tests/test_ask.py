from app.search.ask import NOT_FOUND_MESSAGE, answer_grounded, build_context
from app.search.retrieval import RetrievedChunk


class FakeLLM:
    def __init__(self, response: str = "the answer [1]"):
        self.calls: list[tuple[str, str]] = []
        self.response = response

    def chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        self.calls.append((system, user))
        return self.response


def test_build_context_numbers_sources_in_order():
    chunks = [
        RetrievedChunk(file_path="a.pdf", heading="Intro", text="text A", score=0.9),
        RetrievedChunk(file_path="b.pdf", heading="Summary", text="text B", score=0.8),
    ]

    context, sources = build_context(chunks)

    assert sources == ["a.pdf", "b.pdf"]
    assert "[1]" in context and "a.pdf" in context and "text A" in context
    assert "[2]" in context and "b.pdf" in context and "text B" in context


def test_answer_grounded_returns_not_found_without_llm_call_when_no_chunks():
    llm = FakeLLM()

    result = answer_grounded("what is x?", [], llm)

    assert result.answer == NOT_FOUND_MESSAGE
    assert result.grounded is False
    assert result.sources == []
    assert llm.calls == []


def test_answer_grounded_returns_not_found_when_best_score_below_threshold():
    llm = FakeLLM()
    chunks = [RetrievedChunk(file_path="a.pdf", heading="H", text="t", score=0.1)]

    result = answer_grounded("what is x?", chunks, llm, similarity_threshold=0.3)

    assert result.answer == NOT_FOUND_MESSAGE
    assert result.grounded is False
    assert llm.calls == []


def test_answer_grounded_calls_llm_with_citation_context_when_above_threshold():
    llm = FakeLLM(response="x is y [1]")
    chunks = [
        RetrievedChunk(file_path="a.pdf", heading="H", text="x is y", score=0.9),
        RetrievedChunk(file_path="b.pdf", heading="H2", text="irrelevant", score=0.2),
    ]

    result = answer_grounded("what is x?", chunks, llm, similarity_threshold=0.3)

    assert result.grounded is True
    assert result.answer == "x is y [1]"
    assert result.sources == ["a.pdf", "b.pdf"]
    assert len(llm.calls) == 1
    system, user = llm.calls[0]
    assert "what is x?" in user
    assert "[1]" in user and "a.pdf" in user

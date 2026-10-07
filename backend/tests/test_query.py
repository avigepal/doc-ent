from app.search.query import run_query
from app.search.retrieval import RetrievedChunk


class FakeLLM:
    def __init__(self, response: str = "answer"):
        self.calls: list[tuple[str, str]] = []
        self.response = response

    def chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        self.calls.append((system, user))
        return self.response


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

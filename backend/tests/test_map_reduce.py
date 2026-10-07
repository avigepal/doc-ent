from app.summarization.chunker import Chunk
from app.summarization.map_reduce import map_summarize, reduce_summaries


class FakeLLM:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        self.calls.append((system, user))
        return f"[summary of: {user}]"


def test_map_summarize_calls_llm_once_per_chunk_and_preserves_order():
    chunks = [
        Chunk(index=0, heading="A", text="text A"),
        Chunk(index=1, heading="B", text="text B"),
    ]
    llm = FakeLLM()

    summaries = map_summarize(chunks, llm)

    assert summaries == ["[summary of: text A]", "[summary of: text B]"]
    assert len(llm.calls) == 2


def test_reduce_summaries_returns_single_summary_unchanged_without_llm_call():
    llm = FakeLLM()

    result = reduce_summaries(["only one"], llm)

    assert result == "only one"
    assert llm.calls == []  # no wasted LLM call for a single input


def test_reduce_summaries_empty_list_returns_empty_string():
    llm = FakeLLM()
    assert reduce_summaries([], llm) == ""
    assert llm.calls == []


def test_reduce_summaries_batches_and_reduces_hierarchically():
    llm = FakeLLM()
    summaries = [f"summary {i}" for i in range(5)]

    result = reduce_summaries(summaries, llm, batch_size=2)

    # 5 items, batch_size=2 -> round 1: 3 calls (2,2,1) -> 3 results
    # round 2: 3 results, batch_size=2 -> 2 calls (2,1) -> 2 results
    # round 3: 2 results, batch_size=2 -> 1 call -> 1 final result
    assert len(llm.calls) == 3 + 2 + 1
    assert result.startswith("[summary of:")


def test_reduce_summaries_single_batch_when_all_fit():
    llm = FakeLLM()
    summaries = ["s1", "s2", "s3"]

    result = reduce_summaries(summaries, llm, batch_size=10)

    assert len(llm.calls) == 1
    assert llm.calls[0][1] == "s1\n\ns2\n\ns3"
    assert result == "[summary of: s1\n\ns2\n\ns3]"

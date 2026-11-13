import pandas as pd

from app.search.correlate import (
    cited_sources,
    compute_correlations,
    best_passages_per_file,
    correlate,
    cross_document_correlate,
    format_correlation_matrix,
    group_by_file,
    statistical_correlate,
)
from app.search.retrieval import RetrievedChunk


class FakeLLM:
    def __init__(self, response: str = "narrated findings"):
        self.calls: list[tuple[str, str]] = []
        self.response = response

    def chat(self, system: str, user: str, temperature: float = 0.2) -> str:
        self.calls.append((system, user))
        return self.response


def _chunk(file_path, heading, text, score=0.8):
    return RetrievedChunk(file_path=file_path, heading=heading, text=text, score=score)


# ---------- cross-document mode ----------

def test_group_by_file_preserves_first_seen_order():
    chunks = [
        _chunk("a.pdf", "H1", "t1"),
        _chunk("b.pdf", "H2", "t2"),
        _chunk("a.pdf", "H3", "t3"),
    ]
    grouped = group_by_file(chunks)
    assert list(grouped.keys()) == ["a.pdf", "b.pdf"]
    assert len(grouped["a.pdf"]) == 2


def test_cross_document_correlate_builds_numbered_sections_and_cites_sources():
    llm = FakeLLM(response="Q1 numbers in [1] align with the contract in [2].")
    chunks = [
        _chunk("sales_q1.xlsx", "Totals", "Q1 revenue: 120000"),
        _chunk("contract_acme.docx", "Terms", "Minimum commitment: 100000"),
    ]

    result = cross_document_correlate("how do these relate?", chunks, llm)

    assert result.sources == ["sales_q1.xlsx", "contract_acme.docx"]
    assert result.answer == "Q1 numbers in [1] align with the contract in [2]."
    assert len(llm.calls) == 1
    _, user = llm.calls[0]
    assert "[1]" in user and "sales_q1.xlsx" in user
    assert "[2]" in user and "contract_acme.docx" in user


# ---------- statistical mode ----------

def test_compute_correlations_only_uses_numeric_columns():
    df = pd.DataFrame({"revenue": [1, 2, 3, 4], "cost": [2, 4, 6, 8], "label": ["a", "b", "c", "d"]})
    pairs = compute_correlations({"sheet1": df})
    assert [(p.first, p.second) for p in pairs] == [("revenue", "cost")]
    assert round(pairs[0].r, 2) == 1.0
    assert pairs[0].rows == 4


def test_format_correlation_matrix_reports_strong_pairs_sorted_by_strength():
    df = pd.DataFrame({
        "revenue": [1, 2, 3, 4],
        "cost": [2, 4, 6, 8],      # perfectly correlated with revenue
        "noise": [5, 1, 9, 2],     # weakly/uncorrelated
    })
    pairs = compute_correlations({"sheet1": df})

    summary = format_correlation_matrix(pairs, min_abs=0.5)

    assert "revenue vs cost" in summary
    assert "r=1.00" in summary


def test_format_correlation_matrix_handles_no_strong_pairs():
    df = pd.DataFrame({"a": [1, 2, 3, 4], "b": [4, 1, 3, 2]})
    pairs = compute_correlations({"sheet1": df})

    summary = format_correlation_matrix(pairs, min_abs=0.99)

    assert "No strong correlations" in summary


def test_statistical_correlate_passes_computed_numbers_to_llm_not_raw_rows():
    llm = FakeLLM(response="revenue and cost move together")
    df = pd.DataFrame({"revenue": [1, 2, 3, 4], "cost": [2, 4, 6, 8]})

    result = statistical_correlate("are revenue and cost related?", {"sheet1": df}, llm)

    assert result.answer == "revenue and cost move together"
    assert "r=1.00" in result.correlation_summary
    assert len(llm.calls) == 1
    _, user = llm.calls[0]
    assert "revenue vs cost" in user
    # raw row values should not be dumped into the prompt
    assert "[1, 2, 3, 4]" not in user


# ---------- router: both modes together ----------

def test_correlate_runs_only_cross_doc_when_multi_file_and_no_tables():
    llm = FakeLLM()
    chunks = [_chunk("a.pdf", "H", "t1"), _chunk("b.pdf", "H", "t2")]

    report = correlate("question", chunks, {}, llm)

    assert report.cross_doc is not None
    assert report.statistical is None


def test_correlate_runs_only_statistical_when_single_file_and_tables_present():
    llm = FakeLLM()
    chunks = [_chunk("a.xlsx", "H", "t1")]
    tables = {"sheet1": pd.DataFrame({"x": [1, 2, 3], "y": [2, 4, 6]})}

    report = correlate("question", chunks, tables, llm)

    assert report.cross_doc is None
    assert report.statistical is not None


def test_correlate_runs_both_when_multi_file_and_tables_present():
    llm = FakeLLM()
    chunks = [_chunk("a.xlsx", "H", "t1"), _chunk("b.docx", "H", "t2")]
    tables = {"sheet1": pd.DataFrame({"x": [1, 2, 3], "y": [2, 4, 6]})}

    report = correlate("question", chunks, tables, llm)

    assert report.cross_doc is not None
    assert report.statistical is not None


def test_correlate_runs_neither_when_single_file_and_no_tables():
    llm = FakeLLM()
    chunks = [_chunk("a.pdf", "H", "t1")]

    report = correlate("question", chunks, {}, llm)

    assert report.cross_doc is None
    assert report.statistical is None
    assert llm.calls == []


def test_wants_cross_document_only_for_comparison_style_questions():
    from app.search.correlate import wants_cross_document

    for question in (
        "compare the Mac Studio and the custom PC",
        "Mac Studio vs custom PC",
        "what are the differences between the two quotes?",
        "do these documents contradict each other?",
        "how do these relate?",
        "what trends appear across the documents?",
        "what do both files say about power?",
    ):
        assert wants_cross_document(question), question

    for question in (
        "how do I enable wake-on-lan?",
        "what is python",
        "summarize the api doc",
        "which BIOS settings should I check?",
    ):
        assert not wants_cross_document(question), question


def test_correlate_skips_cross_doc_when_disabled():
    llm = FakeLLM()
    chunks = [_chunk("a.pdf", "H", "x"), _chunk("b.pdf", "H", "y")]

    report = correlate("question", chunks, {}, llm, cross_doc_enabled=False)

    assert report.cross_doc is None
    assert llm.calls == []


# ---------- on-demand comparison (the "Compare across documents" button) ----------

def test_compare_documents_compares_the_matching_files():
    from app.search.correlate import compare_documents

    llm = FakeLLM("they agree [1][2]")
    chunks = [_chunk("a.pdf", "H", "text a"), _chunk("b.pdf", "H", "text b")]

    result = compare_documents("mac vs pc", chunks, llm)

    assert result is not None
    assert result.answer == "they agree [1][2]"
    assert result.sources == ["a.pdf", "b.pdf"]


def test_compare_documents_leaves_out_weakly_matching_files():
    from app.search.correlate import compare_documents

    llm = FakeLLM("both agree [1][2]")
    chunks = [
        _chunk("a.pdf", "H", "text a", score=0.9),
        _chunk("b.pdf", "H", "text b", score=0.8),
        _chunk("unrelated_quote.pdf", "H", "other", score=0.1),
    ]

    result = compare_documents("mac vs pc", chunks, llm)

    assert result.sources == ["a.pdf", "b.pdf"]
    assert "unrelated_quote.pdf" not in llm.calls[0][1]


def test_compare_documents_needs_two_matching_files():
    from app.search.correlate import compare_documents

    llm = FakeLLM()
    only_one_matches = [_chunk("a.pdf", "H", "x", score=0.9), _chunk("b.pdf", "H", "y", score=0.05)]

    assert compare_documents("q", only_one_matches, llm) is None
    assert compare_documents("q", [_chunk("a.pdf", "H", "x"), _chunk("a.pdf", "H2", "y")], llm) is None
    assert llm.calls == []  # no model call when there is nothing to compare


# ---------- numbers: correlation never crosses tables, pairs are structured ----------

def test_compute_correlations_never_pairs_columns_from_different_tables():
    a = pd.DataFrame({"x": [1, 2, 3, 4], "y": [2, 4, 6, 8]})
    b = pd.DataFrame({"p": [1, 2, 3, 4], "q": [9, 1, 8, 2]})
    pairs = compute_correlations({"a": a, "b": b})
    assert {(p.table, p.first, p.second) for p in pairs} == {("a", "x", "y"), ("b", "p", "q")}


def test_compute_correlations_skips_pairs_with_too_few_rows():
    df = pd.DataFrame({"x": [1, 2, None, None], "y": [2, None, 6, None]})
    assert compute_correlations({"t": df}) == []


def test_statistical_result_carries_structured_pairs():
    llm = FakeLLM(response="ok")
    df = pd.DataFrame({"revenue": [1, 2, 3, 4], "cost": [2, 4, 6, 8]})
    result = statistical_correlate("q", {"sheet1": df}, llm)
    assert len(result.pairs) == 1
    assert result.pairs[0].first == "revenue" and result.pairs[0].second == "cost"


# ---------- sources: only the documents the answer cites ----------

def test_cross_doc_sources_are_only_the_cited_documents():
    llm = FakeLLM(response="Budget was approved [1] and the vendor was chosen [3, 4].")
    chunks = [_chunk("a.pdf", "H", "t1"), _chunk("b.pdf", "H", "t2"), _chunk("c.pdf", "H", "t3"), _chunk("d.pdf", "H", "t4")]
    result = cross_document_correlate("q", chunks, llm)
    assert result.sources == ["a.pdf", "c.pdf", "d.pdf"]
    assert result.retrieved == ["a.pdf", "b.pdf", "c.pdf", "d.pdf"]


def test_cited_sources_is_empty_when_the_answer_cites_nothing():
    assert cited_sources("No notable relationships found.", ["a.pdf", "b.pdf"]) == []


# ---------- coverage: one passage per document ----------

def test_best_passages_per_file_keeps_best_passage_from_each_document_up_to_the_limit():
    chunks = [
        _chunk("a.pdf", "H", "a-low"), _chunk("a.pdf", "H", "a-high"),
        _chunk("b.pdf", "H", "b-only"), _chunk("c.pdf", "H", "c-only"),
    ]
    chunks[0] = RetrievedChunk("a.pdf", "H", "a-low", 0.4)
    chunks[1] = RetrievedChunk("a.pdf", "H", "a-high", 0.9)
    chunks[2] = RetrievedChunk("b.pdf", "H", "b-only", 0.6)
    chunks[3] = RetrievedChunk("c.pdf", "H", "c-only", 0.2)
    kept = best_passages_per_file(chunks, max_files=2, per_file=1)
    assert [c.text for c in kept] == ["a-high", "b-only"]


def test_best_passages_per_file_keeps_several_passages_from_a_document_in_order():
    chunks = [
        RetrievedChunk("a.pdf", "H", "a-low", 0.4),
        RetrievedChunk("a.pdf", "H", "a-high", 0.9),
        RetrievedChunk("b.pdf", "H", "b-only", 0.6),
        RetrievedChunk("c.pdf", "H", "c-only", 0.2),
    ]
    kept = best_passages_per_file(chunks, max_files=2, per_file=2)
    assert [c.text for c in kept] == ["a-low", "a-high", "b-only"]

import pandas as pd

from app.search.correlate import (
    compute_correlations,
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
    corr = compute_correlations({"sheet1": df})
    assert set(corr.columns) == {"revenue", "cost"}
    assert round(corr.loc["revenue", "cost"], 2) == 1.0


def test_format_correlation_matrix_reports_strong_pairs_sorted_by_strength():
    df = pd.DataFrame({
        "revenue": [1, 2, 3, 4],
        "cost": [2, 4, 6, 8],      # perfectly correlated with revenue
        "noise": [5, 1, 9, 2],     # weakly/uncorrelated
    })
    corr = compute_correlations({"sheet1": df})

    summary = format_correlation_matrix(corr, min_abs=0.5)

    assert "revenue vs cost" in summary
    assert "r=1.00" in summary


def test_format_correlation_matrix_handles_no_strong_pairs():
    df = pd.DataFrame({"a": [1, 2, 3, 4], "b": [4, 1, 3, 2]})
    corr = compute_correlations({"sheet1": df})

    summary = format_correlation_matrix(corr, min_abs=0.99)

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

    llm = FakeLLM("they agree")
    chunks = [_chunk("a.pdf", "H", "text a"), _chunk("b.pdf", "H", "text b")]

    result = compare_documents("mac vs pc", chunks, llm)

    assert result is not None
    assert result.answer == "they agree"
    assert result.sources == ["a.pdf", "b.pdf"]


def test_compare_documents_leaves_out_weakly_matching_files():
    from app.search.correlate import compare_documents

    llm = FakeLLM()
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

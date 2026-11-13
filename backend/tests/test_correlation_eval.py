import pytest

from app.evaluation.correlation_eval import (
    Case,
    RunOutcome,
    load_cases,
    outcome_from_response,
    score_case,
)


def test_outcome_counts_a_relation_only_when_two_documents_are_cited():
    found = outcome_from_response({"cross_doc": {"answer": "x [1][2]", "sources": ["/data/raw/a/e03.eml", "/data/raw/a/e04.eml"]}})
    one = outcome_from_response({"cross_doc": {"answer": "x [1]", "sources": ["/data/raw/a/e03.eml"]}})
    none = outcome_from_response({"cross_doc": {"answer": "No notable relationships found.", "sources": []}})
    assert found == RunOutcome(relation_found=True, cited=["e03.eml", "e04.eml"])
    assert one.relation_found is False
    assert none == RunOutcome(relation_found=False, cited=[])


def test_outcome_handles_a_missing_cross_doc_block():
    assert outcome_from_response({"cross_doc": None, "statistical": None}) == RunOutcome(False, [])


def test_score_perfect_when_every_run_matches_the_label_and_citations():
    case = Case("q", ["f"], True, ["a.eml", "b.eml"])
    runs = [RunOutcome(True, ["a.eml", "b.eml"])] * 3
    s = score_case(case, runs)
    assert (s.found_ok, s.precision, s.recall, s.consistency) == (1.0, 1.0, 1.0, 1.0)


def test_score_penalises_wrong_citations_and_missed_documents():
    case = Case("q", ["f"], True, ["a.eml", "b.eml"])
    s = score_case(case, [RunOutcome(True, ["a.eml", "c.eml"])])
    assert s.precision == pytest.approx(0.5)
    assert s.recall == pytest.approx(0.5)


def test_consistency_measures_agreement_across_repeats():
    case = Case("q", ["f"], True, ["a.eml"])
    runs = [RunOutcome(True, ["a.eml"]), RunOutcome(True, ["a.eml"]), RunOutcome(False, [])]
    s = score_case(case, runs)
    assert s.consistency == pytest.approx(2 / 3)
    assert s.found_ok == pytest.approx(2 / 3)


def test_negative_case_with_no_relation_scores_found_ok_when_nothing_is_cited():
    case = Case("q", ["f"], False, [])
    s = score_case(case, [RunOutcome(False, [])])
    assert s.found_ok == 1.0
    assert s.recall == 1.0


def test_score_refuses_zero_runs():
    with pytest.raises(ValueError):
        score_case(Case("q", [], True, []), [])


def test_bundled_cases_load():
    cases = load_cases()
    assert len(cases) >= 3
    assert all(isinstance(c.expect_relation, bool) for c in cases)

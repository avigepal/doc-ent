from app.search.hybrid import KEYWORD_MATCH_FLOOR, Candidate, build_or_tsquery, build_tsquery_terms, fuse
from app.search.indexing import build_embedding_input


def _c(chunk_id, similarity, path="a.pdf"):
    return Candidate(chunk_id=chunk_id, file_path=path, heading="H", text=f"t{chunk_id}", similarity=similarity)


def test_terms_drop_stopwords_and_dedupe():
    assert build_tsquery_terms("What is the invoice total for Acme? invoice!") == ["invoice", "total", "acme"]


def test_terms_are_safe_for_to_tsquery():
    assert build_or_tsquery("x'; DROP TABLE files; --") == "drop | table | files"


def test_or_query_none_when_only_stopwords():
    assert build_or_tsquery("what is the") is None


def test_fuse_prefers_chunks_found_by_both_searches():
    vector = [_c(1, 0.9), _c(2, 0.8), _c(3, 0.7)]
    keyword = [_c(3, 0.7), _c(4, 0.1)]
    ids = [c.chunk_id for c, _ in fuse(vector, keyword, k=4)]
    assert ids[0] == 3 or ids[0] == 1  # both-list chunk competes with the top vector hit
    assert set(ids) == {1, 2, 3, 4}
    assert ids.index(3) < ids.index(2)


def test_keyword_hit_gets_score_floor_so_threshold_does_not_reject_it():
    results = dict((c.chunk_id, s) for c, s in fuse([], [_c(7, 0.05)], k=5))
    assert results[7] == KEYWORD_MATCH_FLOOR


def test_vector_only_hit_keeps_raw_similarity():
    results = dict((c.chunk_id, s) for c, s in fuse([_c(1, 0.42)], [], k=5))
    assert results[1] == 0.42


def test_keyword_hit_without_embedding_scores_floor():
    results = dict((c.chunk_id, s) for c, s in fuse([], [_c(9, None)], k=5))
    assert results[9] == KEYWORD_MATCH_FLOOR


def test_fuse_respects_k():
    assert len(fuse([_c(i, 0.5) for i in range(10)], [], k=3)) == 3


def test_embedding_input_includes_metadata_and_heading():
    out = build_embedding_input("/data/raw/hr/budget_2024.pdf", "Budget", "alice", "Q3", "body text")
    assert out == "File: budget_2024.pdf\nTitle: Budget\nAuthor: alice\nSection: Q3\n\nbody text"


def test_embedding_input_skips_missing_metadata_and_untitled():
    out = build_embedding_input("a/b.txt", None, None, "(untitled)", "body")
    assert out == "File: b.txt\n\nbody"

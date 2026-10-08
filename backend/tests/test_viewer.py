from app.search.viewer import highlight_terms


def test_a_question_highlights_its_content_words_only():
    assert highlight_terms("what is the Mac Studio price?", phrase=False) == ["mac", "studio", "price"]


def test_a_phrase_keeps_every_word():
    assert highlight_terms("wake on lan", phrase=True) == ["wake", "on", "lan"]


def test_nothing_to_highlight_without_a_query():
    assert highlight_terms(None, phrase=False) == []
    assert highlight_terms("", phrase=True) == []

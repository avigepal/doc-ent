from app.editing.literal import Replacement, find_spans
from app.editing.textspans import apply_spans, diff_spans


def test_a_replacement_inside_one_piece_leaves_the_others_alone():
    assert apply_spans(["The ", "Mac Studio", " is quiet"], [(4, 14, "Mac Mini")]) == ["The ", "Mac Mini", " is quiet"]


def test_a_replacement_across_pieces_goes_into_the_first_one():
    # "Mac" is regular, "Studio" is bold: the new words take the first piece's style
    assert apply_spans(["the Mac ", "Studio", " now"], [(4, 14, "Mac Mini")]) == ["the Mac Mini", "", " now"]


def test_an_insertion_joins_the_piece_before_it():
    assert apply_spans(["abc", "def"], [(3, 3, "X")]) == ["abcX", "def"]
    assert apply_spans(["abc"], [(0, 0, "X")]) == ["Xabc"]


def test_several_changes_in_one_pass():
    pieces = ["one two ", "one", " three"]
    spans = find_spans("".join(pieces), Replacement("one", "1"))
    assert apply_spans(pieces, spans) == ["1 two ", "1", " three"]


def test_no_text_means_nothing_to_change():
    assert apply_spans([], []) == []
    assert apply_spans(["", ""], [(0, 0, "x")]) == ["", ""]


def test_diff_keeps_the_words_that_did_not_change():
    old = "The Mac Studio is very quiet today."
    new = "The Mac Mini is quiet today."
    spans = diff_spans(old, new)

    assert apply_spans([old], spans)[0] == new
    # only the changed words are touched, not the whole sentence
    assert all(end - start < 15 for start, end, _ in spans)


def test_identical_text_has_no_changes():
    assert diff_spans("same text", "same text") == []

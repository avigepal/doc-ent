from datetime import date

import pytest

from app.search.email_filter import EmailFilter, _folder_of, _words, parse_day


def test_an_empty_day_means_no_limit():
    assert parse_day("") is None
    assert parse_day("   ") is None
    assert parse_day(None) is None


def test_a_day_is_read_as_iso_date():
    assert parse_day("2024-03-31") == date(2024, 3, 31)


@pytest.mark.parametrize("bad", ["31/03/2024", "2024-13-01", "yesterday"])
def test_other_date_formats_are_refused_with_a_hint(bad):
    with pytest.raises(ValueError, match="2024-03-31"):
        parse_day(bad)


def test_a_range_that_ends_before_it_starts_is_refused():
    with pytest.raises(ValueError):
        EmailFilter(after=date(2024, 5, 1), before=date(2024, 4, 1))


def test_a_one_day_range_is_fine():
    EmailFilter(after=date(2024, 5, 1), before=date(2024, 5, 1))


def test_name_words_split_on_spaces_and_ignore_extra_spaces():
    assert _words("  alice   smith ") == ["alice", "smith"]
    assert _words("") == []


def test_folder_of_gives_the_folder_under_raw(tmp_path):
    raw = tmp_path / "raw"
    assert _folder_of(str(raw / "mail" / "2024" / "a.eml"), raw) == "mail/2024"
    assert _folder_of(str(raw / "a.eml"), raw) == ""
    assert _folder_of(str(tmp_path / "elsewhere" / "a.eml"), raw) == ""

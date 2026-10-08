import pytest

from app.search.keyword import (
    FileMatches,
    KeywordQuery,
    KeywordResult,
    Match,
    format_keyword_answer,
    make_snippet,
    parse_keyword_query,
)


@pytest.mark.parametrize(
    "message, words, phrase",
    [
        ("mac", ("mac",), False),
        ("  Mac  ", ("mac",), False),
        ("mac studio", ("mac", "studio"), False),
        ("ZX-90417", ("zx", "90417"), True),  # one token as typed: looked up as written
        ('"wake on lan"', ("wake", "on", "lan"), True),
        ("'M5 Ultra'", ("m5", "ultra"), True),
        ("ROCm", ("rocm",), False),
    ],
)
def test_a_word_or_two_is_a_keyword_lookup(message, words, phrase):
    query = parse_keyword_query(message)
    assert query is not None
    assert query.words == words and query.phrase is phrase


@pytest.mark.parametrize(
    "message",
    [
        "what is the mac studio price?",
        "mac?",
        "how much",
        "mac studio price details",  # three loose words read as a question
        "hello",
        "thanks",
        "more",
        "why",
        "translate this",
        "replace mac",
        "show files",
        "the",
        "",
        "   ",
        "x",
        "a" * 80,
    ],
)
def test_questions_instructions_and_chat_are_not_lookups(message):
    assert parse_keyword_query(message) is None


def test_the_hit_is_bold_and_the_rest_is_markdown_safe():
    query = KeywordQuery(("mac",), False, "mac")
    snippet = make_snippet("Specs: Mac mini M5 Pro a|b 64GB * fast_ram", query)
    assert "**Mac**" in snippet
    assert "\\|" in snippet and "\\*" in snippet and "fast\\_ram" in snippet


def test_a_stemmed_form_is_highlighted_too():
    query = KeywordQuery(("compare",), False, "compare")
    assert "**comparing**" in make_snippet("We are comparing two options", query)


def test_a_long_text_is_cut_around_the_first_hit_at_word_boundaries():
    text = "alpha " * 100 + "needle " + "omega " * 100
    snippet = make_snippet(text, KeywordQuery(("needle",), False, "needle"), radius=30)
    assert "**needle**" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")
    assert len(snippet) < 120
    assert "alph " not in snippet and "omeg " not in snippet  # no word cut in half


def test_text_without_the_literal_term_still_gives_a_snippet():
    query = KeywordQuery(("zzz",), False, "zzz")
    assert make_snippet("some chunk text", query) == "some chunk text"


def test_the_answer_lists_files_counts_and_highlighted_matches():
    query = KeywordQuery(("mac",), False, "mac")
    result = KeywordResult(
        files=[
            FileMatches("/data/raw/a/PC_vs_Mac.pdf", 9, [Match("Specs", "Mac mini M5 Pro"), Match("", "Mac Studio")]),
            FileMatches("/data/raw/b/notes.txt", 1, [Match("", "my mac is old")]),
        ],
        name_only=["/data/raw/c/Mac Studio.pdf"],
    )

    answer, sources = format_keyword_answer(query, result)

    assert "**10 sections mention “mac” in 2 files**" in answer
    assert "**1. PC\\_vs\\_Mac.pdf** — 9 sections" in answer
    assert "- **Mac** mini M5 Pro — *Specs*" in answer
    assert "**2. notes.txt** — 1 section" in answer
    assert "Files with “mac” in the name" in answer and "- Mac Studio.pdf" in answer
    assert "Showing the best 3 sections per file." in answer
    assert sources == ["/data/raw/a/PC_vs_Mac.pdf", "/data/raw/b/notes.txt", "/data/raw/c/Mac Studio.pdf"]


def test_a_file_found_only_by_its_name_is_listed_on_its_own():
    query = KeywordQuery(("mac",), False, "mac")
    answer, sources = format_keyword_answer(query, KeywordResult([], ["/x/Mac.pdf"]))

    assert "mention" not in answer
    assert "Files with “mac” in the name" in answer
    assert sources == ["/x/Mac.pdf"]


def test_table_borders_and_leftover_glyph_placeholders_are_dropped():
    query = KeywordQuery(("mac",), False, "mac")
    snippet = make_snippet("| Spec | Mac mini | GLYPH&lt;127&gt; Verify the MAC address GLYPH<127>", query)

    assert "GLYPH" not in snippet and "\\|" not in snippet
    assert "**Mac**" in snippet and "**MAC**" in snippet


def test_a_phrase_is_highlighted_as_one_hit():
    query = KeywordQuery(("wake", "on", "lan"), True, "wake on lan")
    snippet = make_snippet("Enable Wake-on-LAN now. Turn it on.", query)

    assert snippet == "Enable **Wake-on-LAN** now. Turn it on."


def test_each_match_links_to_its_exact_section():
    query = KeywordQuery(("wake", "on", "lan"), True, "wake on lan")
    result = KeywordResult([FileMatches("/raw/a.pdf", 2, [Match("H", "Wake-on-LAN here", 7)], file_id=12)], ["/raw/My File.pdf"])

    answer, _ = format_keyword_answer(query, result)

    assert "[open](#view?file=12&chunk=7&q=wake%20on%20lan&phrase=1)" in answer
    assert "[open](#view?path=%2Fraw%2FMy%20File.pdf&q=wake%20on%20lan&phrase=1)" in answer

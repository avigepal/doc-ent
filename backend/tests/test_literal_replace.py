import pytest

from app.editing.engine import EditResult, edit_document
from app.editing.literal import Replacement, apply_replacement, parse_replacement


@pytest.mark.parametrize(
    "instruction, old, new",
    [
        # the request that failed in practice
        ("edit this file instead of mac studio i want mac mini", "mac studio", "mac mini"),
        ("replace Mac Studio with Mac Mini", "Mac Studio", "Mac Mini"),
        ("Please replace \"Mac Studio\" with \"Mac Mini\" everywhere", "Mac Studio", "Mac Mini"),
        ("change Acme Corp to Globex Inc in the document", "Acme Corp", "Globex Inc"),
        ("can you swap Alice with Bob", "Alice", "Bob"),
        ("rename Project Falcon to Project Eagle", "Project Falcon", "Project Eagle"),
        ("use Mac Mini instead of Mac Studio", "Mac Studio", "Mac Mini"),
        ("write Python 3.12 instead of Python 3.9 throughout the file", "Python 3.9", "Python 3.12"),
        ("update this document, instead of Dell I need HP.", "Dell", "HP"),
        ("v1.0 -> v2.0", "v1.0", "v2.0"),
        ("replace all occurrences of ZX-90417 with ZX-90418", "ZX-90417", "ZX-90418"),
    ],
)
def test_plain_replacements_are_recognized(instruction, old, new):
    assert parse_replacement(instruction) == Replacement(old, new)


@pytest.mark.parametrize(
    "instruction",
    [
        "translate this to Hindi",
        "make it more formal",
        "replace the table with a bullet list",  # describes a change, not a name
        "change the tone to formal",
        "change this heading to something shorter",
        "use a table instead of bullet points",
        "replace Mac Studio with Mac Mini and translate it to French",  # a second instruction
        "instead of Mac Studio I want Mac Mini and also add a summary at the top",
        "fix the grammar",
        "replace Foo with foo",  # only the case differs
    ],
)
def test_anything_else_is_left_to_the_model(instruction):
    assert parse_replacement(instruction) is None


def test_every_occurrence_changes_including_the_title_and_the_body():
    text = "# Tell me about Mac Studio\n\nThe Mac Studio is compact. Why choose the Mac Studio?"
    new, count = apply_replacement(text, Replacement("mac studio", "mac mini"))

    assert count == 3
    assert new == "# Tell me about Mac Mini\n\nThe Mac Mini is compact. Why choose the Mac Mini?"


def test_the_capitalization_style_of_each_match_is_kept():
    text = "MAC STUDIO, Mac Studio, mac studio"
    new, _ = apply_replacement(text, Replacement("Mac Studio", "Mac Mini"))
    assert new == "MAC MINI, Mac Mini, mac mini"


def test_a_name_wrapped_over_two_lines_is_still_found():
    new, count = apply_replacement("the Mac\nStudio is quiet", Replacement("Mac Studio", "Mac Mini"))
    assert count == 1 and new == "the Mac Mini is quiet"


def test_only_whole_words_are_replaced():
    new, count = apply_replacement("Studios, Mac Studios and Mac Studio", Replacement("Mac Studio", "Mac Mini"))
    assert count == 1 and new == "Studios, Mac Studios and Mac Mini"


def test_regex_characters_in_the_names_are_taken_literally():
    new, count = apply_replacement("price (USD) 5.99; price (EUR)", Replacement("(USD) 5.99", "(INR) 450"))
    assert count == 1 and new == "price (INR) 450; price (EUR)"


class NeverCalledLLM:
    def chat_stream(self, *args, **kwargs):
        raise AssertionError("a plain replacement must not call the model")


def test_a_replacement_request_is_done_in_code_without_the_model():
    updates = list(edit_document("# Mac Studio\n\nThe Mac Studio rocks.", "instead of mac studio i want mac mini", NeverCalledLLM()))

    result = updates[-1]
    assert isinstance(result, EditResult)
    assert result.text == "# Mac Mini\n\nThe Mac Mini rocks."
    assert result.replacements == 2
    assert result.replaced == Replacement("mac studio", "mac mini")


def test_replacing_a_name_that_is_not_in_the_file_says_so_instead_of_guessing():
    with pytest.raises(ValueError, match="doesn't appear"):
        list(edit_document("# Report\n\nNothing here.", "replace Mac Studio with Mac Mini", NeverCalledLLM()))


def test_a_style_instruction_that_looks_like_a_replacement_still_goes_to_the_model():
    class RecordingLLM:
        def __init__(self):
            self.called = False

        def chat_stream(self, system, user, temperature=0.2, **kwargs):
            self.called = True
            yield "Formal text."

    llm = RecordingLLM()
    updates = list(edit_document("Some casual text.", "change the tone to formal", llm))

    assert llm.called
    assert updates[-1].text == "Formal text."


def test_the_model_prompt_says_a_change_applies_to_every_occurrence():
    from app.editing.engine import EDIT_SYSTEM_PROMPT

    assert "EVERY occurrence" in EDIT_SYSTEM_PROMPT

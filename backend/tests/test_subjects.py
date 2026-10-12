from app.search.report import (
    MERGE_SYSTEM_PROMPT,
    CONDENSE_SYSTEM_PROMPT,
    MAP_SYSTEM_PROMPT,
    ReportDocument,
    ReportLimits,
    group_blocks,
    notes_prompt,
    report_events,
)
from app.search.subjects import (
    Subject,
    SubjectHits,
    describe_duration,
    extract_subjects,
    format_plan,
    parse_subjects,
    rank_files,
    subjects_hint,
)


# ---------- reading the model's reply ----------

def test_subjects_and_aliases_are_read_from_json_even_with_extra_text():
    reply = 'Sure: {"subjects": [{"name": "Asha Rao", "aliases": ["A. Rao", "asha rao"]}, {"name": "Acme Ltd"}]}'

    assert parse_subjects(reply) == [Subject("Asha Rao", ("A. Rao",)), Subject("Acme Ltd")]


def test_bare_strings_count_as_subjects_and_junk_is_dropped():
    assert parse_subjects('{"subjects": ["Mr Shah", "", 7, {"name": "x"}]}') == [Subject("Mr Shah")]


def test_at_most_three_subjects():
    reply = '{"subjects": [' + ",".join(f'{{"name": "Name {i}"}}' for i in range(6)) + "]}"
    assert len(parse_subjects(reply)) == 3


def test_a_malformed_reply_means_no_subject():
    assert parse_subjects("no idea") == []
    assert parse_subjects('{"subjects": "Asha"}') == []
    assert parse_subjects("{broken") == []


def test_a_failing_model_means_no_subject():
    class Boom:
        def chat(self, *args, **kwargs):
            raise RuntimeError("down")

    assert extract_subjects("report on Asha", Boom()) == []


# ---------- ranking and the plan ----------

def _hits():
    hits = SubjectHits()
    hits.paths = {1: "/b.pdf", 2: "/a.pdf", 3: "/c.pdf", 4: "/d.pdf"}
    hits.counts = [{1: 5, 2: 1, 4: 9}, {2: 1, 3: 4}]  # subject A: files 1, 2, 4; subject B: files 2, 3
    return hits


def test_documents_that_mention_every_subject_come_first_then_the_most_mentions():
    assert rank_files(_hits()) == [2, 4, 1, 3]


def test_a_single_subject_ranks_by_mentions():
    hits = SubjectHits()
    hits.paths = {1: "/a.pdf", 2: "/b.pdf"}
    hits.counts = [{1: 1, 2: 3}]
    assert rank_files(hits) == [2, 1]


def test_duration_is_given_in_minutes_then_hours():
    assert describe_duration(5) == "about 1 minute"
    assert describe_duration(25 * 60) == "about 25 minutes"
    assert describe_duration(90 * 60) == "about 1 h 30 min"


def test_the_plan_says_how_many_documents_and_that_it_waits_for_a_go_ahead():
    plan = format_plan([Subject("Asha Rao"), Subject("Acme Ltd")], found=500, reading=300, in_all_subjects=40, seconds=1800)

    assert "500 documents" in plan and "40 of them mention both" in plan
    assert "best 300" in plan or "300 best" in plan
    assert "about 30 minutes" in plan and "Read them all" in plan


def test_only_two_or_more_subjects_get_a_connections_section():
    assert subjects_hint([Subject("Asha Rao")]) == ""
    hint = subjects_hint([Subject("Asha Rao"), Subject("Acme Ltd")])
    assert "Connections between Asha Rao and Acme Ltd" in hint and "no direct link" in hint


# ---------- merging many documents' notes ----------

class MergeLLM:
    """Notes of 1100 characters per document; merging keeps 40 characters of a group."""

    def __init__(self):
        self.calls = []

    def chat(self, system, user, temperature=0.2, **kwargs):
        self.calls.append((system, user))
        if system == MAP_SYSTEM_PROMPT:
            return "- fact [1] " * 100
        if system == MERGE_SYSTEM_PROMPT:
            return user[:40]
        if system == CONDENSE_SYSTEM_PROMPT:
            raise AssertionError("per-document condensing must not shrink each of many documents")
        raise AssertionError(system)

    def chat_stream(self, system, user, temperature=0.2):
        self.calls.append((system, user))
        yield "report"


def test_many_documents_are_merged_in_layers_and_the_report_still_cites_them():
    docs = [ReportDocument(f"/data/{i}.pdf", [("", "text")], i) for i in range(12)]
    limits = ReportLimits(batch_chars=3000, max_files=50, max_chars=10_000, notes_chars=4000)
    llm = MergeLLM()

    events = list(report_events("report on x", docs, llm, limits))

    merges = [c for c in llm.calls if c[0] == MERGE_SYSTEM_PROMPT]
    assert merges
    assert any("Combining the notes" in e["data"]["text"] for e in events if e["event"] == "status")
    final_prompt = llm.calls[-1][1]
    assert "Documents:" in final_prompt and "[12] 11.pdf" in final_prompt
    assert len(final_prompt) < 4000 + 2000  # the merged notes fit the budget, plus the document list
    assert "".join(e["data"]["text"] for e in events if e["event"] == "token") == "report"


def test_the_two_subject_instruction_is_added_to_the_report_prompt():
    docs = [ReportDocument("/data/a.pdf", [("", "text")], 1)]
    limits = ReportLimits(batch_chars=3000, max_files=50, max_chars=10_000, notes_chars=4000)
    llm = MergeLLM()

    list(report_events("link between A and B", docs, llm, limits, None, subjects_hint([Subject("A"), Subject("B")])))

    system = [c for c in llm.calls if c[0] not in (MAP_SYSTEM_PROMPT, MERGE_SYSTEM_PROMPT)][-1][0]
    assert "Connections between A and B" in system


def test_blocks_are_grouped_up_to_a_size_and_an_oversized_block_stands_alone():
    groups = group_blocks(["a" * 40, "b" * 40, "c" * 200, "d" * 10], 100)

    assert [len(g) for g in groups] == [2, 1, 1]


def test_notes_prompt_without_merging_is_unchanged():
    prompt = notes_prompt("req", {"a.pdf": "- x"}, [])
    assert "[1] a.pdf\n- x" in prompt and "Documents:" not in prompt

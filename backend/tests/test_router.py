import pytest

from app.routing.router import decide_route, looks_like_edit, looks_like_question


class FakeRouterLLM:
    def __init__(self, reply="", fail_first=False, always_fail=False):
        self.reply = reply
        self.fail_first = fail_first
        self.always_fail = always_fail
        self.calls = []

    def chat(self, system, user, temperature=0.2, **kwargs):
        self.calls.append({"system": system, "user": user, "temperature": temperature, **kwargs})
        if self.always_fail or (self.fail_first and len(self.calls) == 1):
            raise RuntimeError("server unavailable")
        return self.reply


@pytest.mark.parametrize(
    "message",
    [
        "rewrite this in a formal tone",
        "Please translate it to Hindi",
        "can you fix the grammar?",
        "could you please shorten this document",
        "I want you to remove the second section",
        "make it more concise",
        "add a summary section at the top",
        "convert this to a table",
        "proofread the file",
        "generate a new version with the dates updated",
        "give me a new file with only the pricing",
        "save it as a cleaned copy",
    ],
)
def test_edit_requests_are_recognized(message):
    assert looks_like_edit(message)


@pytest.mark.parametrize(
    "message",
    [
        "what is the order id?",
        "how do I enable wake-on-lan",
        "summarize this document",
        "who wrote the report?",
        "explain the second section",
        "list all files",
        "can you explain this?",
    ],
)
def test_questions_are_not_edits(message):
    assert not looks_like_edit(message)


def test_question_detection():
    assert looks_like_question("what is this")
    assert looks_like_question("is it ready?")
    assert looks_like_question("tell me about the plan")
    assert not looks_like_question("thanks")


def test_attached_file_plus_edit_verb_goes_to_edit_without_a_model_call():
    llm = FakeRouterLLM()
    decision = decide_route("translate this to French", has_attachments=True, history=[], llm=llm)
    assert (decision.action, decision.source) == ("edit", "rules")
    assert llm.calls == []


def test_a_plain_first_question_goes_to_search_without_a_model_call():
    llm = FakeRouterLLM()
    decision = decide_route("what is the order id?", has_attachments=False, history=[], llm=llm)
    assert (decision.action, decision.query, decision.source) == ("search", "what is the order id?", "rules")
    assert llm.calls == []


def test_a_question_about_attached_files_needs_no_model_call():
    llm = FakeRouterLLM()
    decision = decide_route("how do I find the interface?", has_attachments=True, history=[], llm=llm)
    assert decision.action == "search" and llm.calls == []


def test_a_follow_up_is_rewritten_into_a_standalone_query_by_the_model():
    llm = FakeRouterLLM('{"action": "search", "query": "More details about ErP and Wake-on-LAN"}')
    history = [("how do I enable wake-on-lan?", "Run ethtool... [1]")]

    decision = decide_route("give me more information about this", has_attachments=False, history=history, llm=llm)

    assert (decision.action, decision.query, decision.source) == (
        "search",
        "More details about ErP and Wake-on-LAN",
        "model",
    )
    # the model was shown the earlier turn so it could resolve "this"
    assert "how do I enable wake-on-lan?" in llm.calls[0]["user"]
    assert "give me more information about this" in llm.calls[0]["user"]


def test_the_router_call_is_short_and_asks_for_no_thinking():
    llm = FakeRouterLLM('{"action": "search", "query": "q"}')
    decide_route("and the second one?", has_attachments=False, history=[("a", "b")], llm=llm)

    call = llm.calls[0]
    assert call["max_tokens"] <= 300
    assert call["temperature"] == 0.0
    assert call["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert call["extra_body"]["response_format"]["schema"]["required"] == ["action", "query"]


def test_small_talk_goes_to_chat():
    llm = FakeRouterLLM('{"action": "chat", "query": "thanks!"}')
    decision = decide_route("thanks!", has_attachments=False, history=[("a", "b")], llm=llm)
    assert decision.action == "chat"


def test_chat_is_never_chosen_when_files_are_attached():
    llm = FakeRouterLLM('{"action": "chat", "query": "ok"}')
    decision = decide_route("ok", has_attachments=True, history=[], llm=llm)
    assert decision.action == "search"


def test_edit_without_an_attached_file_asks_for_the_file():
    llm = FakeRouterLLM('{"action": "edit", "query": "rewrite it"}')
    decision = decide_route("rewrite it", has_attachments=False, history=[("a", "b")], llm=llm)
    assert decision.action == "need_file"


def test_json_wrapped_in_extra_text_is_still_parsed():
    llm = FakeRouterLLM('Sure! ```json\n{"action": "search", "query": "pricing details"}\n```')
    decision = decide_route("and pricing?", has_attachments=False, history=[("a", "b")], llm=llm)
    assert decision.query == "pricing details"


@pytest.mark.parametrize(
    "reply",
    ["", "not json at all", '{"action": "delete_everything", "query": "x"}', '["search"]', '{"action": "search"}'],
)
def test_a_bad_router_reply_falls_back_to_a_plain_search(reply):
    llm = FakeRouterLLM(reply)
    decision = decide_route("more on that", has_attachments=False, history=[("a", "b")], llm=llm)
    assert decision.action == "search"
    assert decision.query == "more on that"


def test_a_failing_model_falls_back_to_search_of_the_original_message():
    llm = FakeRouterLLM(always_fail=True)
    decision = decide_route("more on that", has_attachments=False, history=[("a", "b")], llm=llm)
    assert (decision.action, decision.query, decision.source) == ("search", "more on that", "fallback")


def test_a_server_that_rejects_response_format_is_retried_without_it():
    llm = FakeRouterLLM('{"action": "search", "query": "rewritten"}', fail_first=True)
    decision = decide_route("more on that", has_attachments=False, history=[("a", "b")], llm=llm)

    assert decision.query == "rewritten"
    assert len(llm.calls) == 2
    assert "response_format" in llm.calls[0]["extra_body"]
    assert "response_format" not in llm.calls[1]["extra_body"]


def test_an_absurdly_long_rewrite_is_ignored():
    llm = FakeRouterLLM('{"action": "search", "query": "' + "x" * 900 + '"}')
    decision = decide_route("more", has_attachments=False, history=[("a", "b")], llm=llm)
    assert decision.query == "more"


@pytest.mark.parametrize(
    "message",
    ["translate this document to French", "rewrite my resume in a formal tone", "please fix the attached report"],
)
def test_an_edit_request_about_a_file_with_nothing_attached_asks_for_the_file(message):
    llm = FakeRouterLLM()
    decision = decide_route(message, has_attachments=False, history=[], llm=llm)
    assert (decision.action, decision.source) == ("need_file", "rules")
    assert llm.calls == []


def test_an_edit_sounding_search_with_no_file_mentioned_is_still_a_search():
    llm = FakeRouterLLM()
    decision = decide_route("update the pricing policy for 2026", has_attachments=False, history=[], llm=llm)
    assert decision.action == "search"


@pytest.mark.parametrize("message", ["summarize this file", "what does the document say?", "explain this pdf"])
def test_a_question_about_the_attached_file_is_a_search_even_mid_conversation(message):
    llm = FakeRouterLLM('{"action": "edit", "query": "x"}')
    decision = decide_route(message, has_attachments=True, history=[("a", "b")], llm=llm)
    assert (decision.action, decision.source) == ("search", "rules")
    assert llm.calls == []


def test_the_model_cannot_turn_a_question_into_an_edit():
    llm = FakeRouterLLM('{"action": "edit", "query": "summarize it"}')
    decision = decide_route("summarize it", has_attachments=True, history=[("a", "b")], llm=llm)
    assert decision.action == "search"


@pytest.mark.parametrize("message", ["mad", "ok", "hmm", "mac studio", "the blue thing"])
def test_the_model_cannot_call_a_bare_word_an_edit(message):
    llm = FakeRouterLLM('{"action": "edit", "query": "x"}')
    decision = decide_route(message, has_attachments=True, history=[("a", "b")], llm=llm)
    assert decision.action == "search"


def test_a_replacement_phrased_without_an_edit_verb_is_still_an_edit_when_the_model_says_so():
    llm = FakeRouterLLM('{"action": "edit", "query": "x"}')
    decision = decide_route("Mac Mini instead of Mac Studio", has_attachments=True, history=[("a", "b")], llm=llm)
    assert decision.action == "edit"


@pytest.mark.parametrize(
    "message", ["make the headings dark blue", "use Arial 11 for the body", "centre the title", "set the margins to 1 inch"]
)
def test_styling_requests_with_a_file_go_to_edit_without_a_model_call(message):
    llm = FakeRouterLLM()
    decision = decide_route(message, has_attachments=True, history=[("a", "b")], llm=llm)
    assert (decision.action, decision.source) == ("edit", "rules")
    assert llm.calls == []


def test_a_styling_request_about_a_file_that_is_not_attached_asks_for_it():
    decision = decide_route("make the headings of this document blue", has_attachments=False, history=[], llm=FakeRouterLLM())
    assert decision.action == "need_file"

import pytest

from app.search.ask import build_context
from app.search.catalog import CatalogEntry, catalog_events, format_catalog_answer, is_catalog_question
from app.search.retrieval import RetrievedChunk


@pytest.mark.parametrize(
    "question",
    [
        "write me all files name",
        "WRITE ME ALL FILES NAME",
        "list all files",
        "list all the documents in this folder",
        "show every document",
        "give me the names of the files",
        "what are the file names?",
        "how many documents do I have?",
        "how many files are uploaded",
        "which documents are uploaded?",
        "what files do I have",
        "list files",
        "show documents",
    ],
)
def test_library_questions_are_detected(question):
    assert is_catalog_question(question)


@pytest.mark.parametrize(
    "question",
    [
        "what is python",
        "summarize the Raspberry Pi document",
        "what does the API doc say about verification?",
        "list all files that mention wake-on-lan",
        "show all documents about the pipeline",
        "which documents contain the word tailscale",
        "give me a summary about powering a raspberry pi",
    ],
)
def test_content_questions_are_not_hijacked(question):
    assert not is_catalog_question(question)


def _entry(path, status="summarized", title=None, author=None, pages=None):
    return CatalogEntry(path=path, status=status, title=title, author=author, page_count=pages)


def test_catalog_answer_lists_every_file_sorted_with_details():
    entries = [
        _entry("/raw/test/b.pdf", pages=3, author="alice"),
        _entry("/raw/test/a.pdf", status="discovered"),
    ]

    answer, sources = format_catalog_answer(entries, ["test"])

    assert answer.startswith('There are 2 documents in folder "test" (1 searchable now).')
    assert answer.index("1. a.pdf") < answer.index("2. b.pdf")
    assert "still processing" in answer
    assert "by alice, 3 pages" in answer
    assert sources == ["/raw/test/a.pdf", "/raw/test/b.pdf"]


def test_catalog_answer_for_empty_scope_says_so():
    answer, sources = format_catalog_answer([], None)
    assert answer == "No documents found in all folders."
    assert sources == []


def test_catalog_answer_handles_windows_paths_and_singular():
    windows_path = chr(92).join(["D:", "raw", "test", "only.pdf"])
    answer, _ = format_catalog_answer([_entry(windows_path)], None)

    assert answer.startswith("There is 1 document in all folders.")
    assert "1. only.pdf" in answer


def test_catalog_events_match_stream_query_shape():
    events = list(catalog_events("the list", ["a.pdf"]))
    assert [e["event"] for e in events] == ["meta", "token", "extra"]
    assert events[0]["data"] == {"sources": ["a.pdf"], "grounded": True}
    assert events[1]["data"] == {"text": "the list"}


def test_build_context_merges_chunks_of_one_file_into_one_source():
    chunks = [
        RetrievedChunk(file_path="a.pdf", heading="Intro", text="first", score=0.9),
        RetrievedChunk(file_path="b.pdf", heading="Other", text="second", score=0.8),
        RetrievedChunk(file_path="a.pdf", heading="Setup", text="third", score=0.7),
    ]

    context, sources = build_context(chunks)

    assert sources == ["a.pdf", "b.pdf"]
    assert context.count("(from a.pdf") == 1
    assert "Intro / Setup" in context
    assert "first" in context and "third" in context


def test_catalog_answer_for_attached_files_says_so():
    answer, _ = format_catalog_answer([_entry("/raw/uploads/a.pdf")], ["ignored-folder"], attached=True)
    assert answer.startswith("There is 1 document in your attached files.")

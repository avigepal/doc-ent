from pathlib import Path
from types import SimpleNamespace

from app.editing.formats import write_output
from app.editing.service import EDITED_TEXT_NAME, LAYOUT_FLAG_NAME, _previous, wants_original
from app.history.serialization import generated_file_to_dict, query_record_to_detail


def test_the_second_edit_in_a_row_gets_its_own_file_name(tmp_path):
    runner = lambda cmd: 0
    first = write_output("a", "txt", tmp_path / "1", "report", runner)
    second = write_output("b", "txt", tmp_path / "2", "report", runner, version=2)
    third = write_output("c", "txt", tmp_path / "3", "report", runner, version=3)

    assert (first.name, second.name, third.name) == ("report-edited.txt", "report-edited-2.txt", "report-edited-3.txt")


def test_the_newest_edit_that_can_be_continued_is_found(tmp_path):
    newest, older, layout = tmp_path / "new", tmp_path / "old", tmp_path / "lay"
    for folder in (newest, older, layout):
        folder.mkdir()
    (older / EDITED_TEXT_NAME).write_text("older text")  # the newest one's text is gone
    (layout / LAYOUT_FLAG_NAME).write_text("1")
    (layout / "a-edited-3.pdf").write_bytes(b"%PDF")

    records = [
        SimpleNamespace(stored_path=str(newest / "a-edited-2.pdf")),
        SimpleNamespace(stored_path=str(older / "a-edited.pdf")),
    ]
    previous = _previous(records)
    assert previous.record is records[1] and previous.kind == "text" and previous.path.read_text() == "older text"

    lay = _previous([SimpleNamespace(stored_path=str(layout / "a-edited-3.pdf"))])
    assert lay.kind == "layout" and lay.path.name == "a-edited-3.pdf"

    assert _previous([]) is None
    assert _previous([SimpleNamespace(stored_path=str(newest / "x.pdf"))]) is None


def test_asking_for_the_original_starts_over():
    assert wants_original("translate it again, from the original file")
    assert wants_original("start over and make it shorter")
    assert wants_original("use the original version")
    assert not wants_original("now make it shorter")
    assert not wants_original("translate it to Hindi")


def test_a_restored_reply_gets_the_same_file_card_as_a_live_one():
    record = SimpleNamespace(
        id=14, stored_path="/data/outputs/ab/report-edited-2.pdf", fmt="pdf", size_bytes=1500, source_name="report-edited.pdf"
    )
    assert generated_file_to_dict(record) == {
        "id": 14,
        "name": "report-edited-2.pdf",
        "fmt": "pdf",
        "size_bytes": 1500,
        "source": "report-edited.pdf",
        "download_path": "/history/exports/14/download",
    }


def test_history_detail_lists_the_files_a_reply_made():
    query = SimpleNamespace(
        id=3, question="q", grounded=True, sources=[], filter_folders=[], filter_author=None, filter_title=None,
        attached_filenames=[], chat_only=False, conversation_id="c", created_at=None, answer="a", cross_doc=None,
        statistical=None,
    )
    export = SimpleNamespace(id=9, stored_path="/x/y.docx", fmt="docx", size_bytes=1, source_name=None)

    assert query_record_to_detail(query)["files"] == []
    assert query_record_to_detail(query, [export])["files"][0]["source"] == ""

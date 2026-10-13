"""The Documents page's folder tree and folder filter, on a live Postgres inside a transaction
that is rolled back (skipped when no database is reachable)."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import settings
from app.ingestion.documents_query import clean_folder_path, folder_children, list_documents
from app.models import FileRecord

RAW = "/zz_tree_test/raw"


@pytest.fixture()
def session():
    try:
        engine = create_engine(settings.database_url, connect_args={"connect_timeout": 2})
        connection = engine.connect()
        connection.execute(text("select 1"))
        connection.rollback()
    except Exception:
        pytest.skip("no Postgres reachable")
    transaction = connection.begin()
    db = Session(bind=connection)
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def _add(db, relative, status="converted"):
    db.add(
        FileRecord(
            path=f"{RAW}/{relative}",
            sha256=f"h-{relative}",
            mime_type="application/pdf",
            size_bytes=10,
            queue="convert_fast",
            status=status,
        )
    )


@pytest.fixture()
def corpus(session):
    for relative, status in [
        ("pgt/a.pdf", "converted"),
        ("pgt/reports/q1.pdf", "converted"),
        ("pgt/reports/q2.pdf", "discovered"),
        ("pgt/reports/2024/jan.pdf", "failed"),
        ("pgt/other/x.pdf", "converted"),
        ("legal_docs/lease.pdf", "converted"),  # "_" is a LIKE wildcard: must not match "legalXdocs"
        ("legalXdocs/other.pdf", "converted"),
        ("100%/odd.pdf", "converted"),
        ("uploads/chat.pdf", "converted"),  # a chat's attachment: not part of the library
        ("loose.pdf", "converted"),  # directly in raw/
    ]:
        _add(session, relative, status)
    session.flush()
    return session


def _names(result):
    return {f["name"]: f for f in result["folders"]}


def test_the_top_level_lists_folders_with_totals_and_hides_uploads(corpus):
    top = folder_children(corpus, RAW, "")
    folders = _names(top)

    assert set(folders) == {"100%", "legalXdocs", "legal_docs", "pgt"}  # no "uploads"
    assert folders["pgt"]["total"] == 5
    assert folders["pgt"]["pending"] == 1 and folders["pgt"]["failed"] == 1
    assert folders["pgt"]["has_subfolders"] is True
    assert folders["legal_docs"]["has_subfolders"] is False
    assert top["files_here"] == 1  # loose.pdf


def test_a_folder_opens_one_level_at_a_time(corpus):
    pgt = folder_children(corpus, RAW, "pgt")
    folders = _names(pgt)

    assert pgt["parent"] == "pgt" and pgt["files_here"] == 1  # a.pdf
    assert set(folders) == {"reports", "other"}
    assert folders["reports"]["path"] == "pgt/reports" and folders["reports"]["total"] == 3
    assert folders["reports"]["has_subfolders"] is True and folders["other"]["has_subfolders"] is False

    reports = folder_children(corpus, RAW, "pgt/reports")
    assert set(_names(reports)) == {"2024"} and reports["files_here"] == 2
    assert _names(reports)["2024"]["failed"] == 1


def test_folder_names_with_wildcard_characters_count_only_their_own_files(corpus):
    assert folder_children(corpus, RAW, "legal_docs")["files_here"] == 1
    assert folder_children(corpus, RAW, "100%")["files_here"] == 1


def test_filtering_by_a_nested_folder_includes_its_subfolders(corpus):
    docs, total = list_documents(corpus, raw_dir=RAW, folder="pgt/reports", limit=50)
    assert total == 3
    assert {d["path"] for d in docs} == {"pgt/reports/q1.pdf", "pgt/reports/q2.pdf", "pgt/reports/2024/jan.pdf"}


def test_a_folder_filter_does_not_match_look_alike_names(corpus):
    docs, total = list_documents(corpus, raw_dir=RAW, folder="legal_docs", limit=50)
    assert total == 1 and docs[0]["path"] == "legal_docs/lease.pdf"


def test_the_uploads_folder_never_appears_in_the_library(corpus):
    docs, _ = list_documents(corpus, raw_dir=RAW, limit=500)
    paths = {d["path"] for d in docs}
    assert "uploads/chat.pdf" not in paths
    assert {"pgt/a.pdf", "loose.pdf", "legal_docs/lease.pdf"} <= paths  # the rest of the corpus is there
    # not even when asked for by name
    assert list_documents(corpus, raw_dir=RAW, folder="uploads", limit=50)[1] == 0


def test_a_path_that_climbs_out_of_raw_matches_nothing(corpus):
    assert list_documents(corpus, raw_dir=RAW, folder="../raw", limit=50)[1] == 0
    assert folder_children(corpus, RAW, "../raw")["folders"] == []


def test_folder_paths_are_cleaned():
    assert clean_folder_path("/pgt//reports/") == ["pgt", "reports"]
    assert clean_folder_path("pgt\\reports") == ["pgt", "reports"]
    assert clean_folder_path("") == [] and clean_folder_path(None) == []

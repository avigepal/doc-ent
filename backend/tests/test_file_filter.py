from sqlalchemy.dialects import postgresql

from app.search.pgvector_retrieval import file_filter_conditions


def _sql(conditions) -> str:
    return " AND ".join(str(c.compile(dialect=postgresql.dialect())) for c in conditions)


def test_attached_file_ids_restrict_the_search_to_those_files():
    sql = _sql(file_filter_conditions("/raw", None, None, None, [3, 4]))
    assert "files.id IN" in sql


def test_attached_files_override_the_folder_scope():
    sql = _sql(file_filter_conditions("/raw", ["contracts"], None, None, [3]))
    assert "files.id IN" in sql
    assert "LIKE" not in sql


def test_without_attachments_the_folder_scope_applies_as_before():
    sql = _sql(file_filter_conditions("/raw", ["contracts"], None, None))
    assert "LIKE" in sql
    assert "files.id IN" not in sql


def test_author_and_title_filters_still_combine_with_attachments():
    sql = _sql(file_filter_conditions("/raw", None, "alice", "plan", [1]))
    assert "files.id IN" in sql and sql.count("ILIKE") == 2

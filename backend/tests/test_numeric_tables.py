import numpy as np
import pandas as pd
import pytest

from app.search.correlate import compute_correlations
from app.search.numeric_tables import read_numeric_tables


def test_csv_numeric_columns_become_one_table_and_text_columns_are_dropped(tmp_path):
    path = tmp_path / "sales.csv"
    pd.DataFrame({"month": ["jan", "feb", "mar", "apr"], "revenue": [1, 2, 3, 4], "cost": [2, 4, 6, 8]}).to_csv(path, index=False)
    tables = read_numeric_tables(str(path))
    assert list(tables) == ["sales.csv / sales.csv"]
    assert list(tables["sales.csv / sales.csv"].columns) == ["revenue", "cost"]


def test_xlsx_each_sheet_is_its_own_table(tmp_path):
    path = tmp_path / "book.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"x": [1, 2, 3, 4], "y": [2, 4, 6, 9]}).to_excel(writer, sheet_name="Q1", index=False)
        pd.DataFrame({"p": [4, 3, 2, 1], "q": [1, 1, 2, 2]}).to_excel(writer, sheet_name="Q2", index=False)
    tables = read_numeric_tables(str(path))
    assert set(tables) == {"book.xlsx / Q1", "book.xlsx / Q2"}


def test_sheet_with_one_numeric_column_is_skipped(tmp_path):
    path = tmp_path / "one.csv"
    pd.DataFrame({"name": ["a", "b", "c"], "score": [1, 2, 3]}).to_csv(path, index=False)
    assert read_numeric_tables(str(path)) == {}


def test_unsupported_suffix_returns_nothing(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("1,2\n3,4\n", encoding="utf-8")
    assert read_numeric_tables(str(path)) == {}


def test_correlations_match_numpy_exactly(tmp_path):
    # Independent check: the reported Pearson r must equal numpy's, for every pair, to rounding error.
    rng = np.random.default_rng(7)
    frame = pd.DataFrame({
        "a": rng.normal(size=50),
        "b": rng.normal(size=50),
        "c": rng.normal(size=50),
    })
    frame["d"] = 0.8 * frame["a"] + 0.2 * rng.normal(size=50)
    expected = np.corrcoef(frame[["a", "b", "c", "d"]].to_numpy(), rowvar=False)
    cols = ["a", "b", "c", "d"]

    pairs = compute_correlations({"t": frame})

    assert len(pairs) == 6
    for p in pairs:
        i, j = cols.index(p.first), cols.index(p.second)
        assert p.r == pytest.approx(expected[i, j], abs=1e-9)
        assert p.rows == 50

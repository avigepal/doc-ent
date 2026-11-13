"""The numeric tables statistical correlation runs on: every spreadsheet and CSV file in the scope.

Each sheet becomes one DataFrame, keyed "file / sheet", holding only its numeric columns. Only numbers
leave this module; the LLM never sees the rows (see app/search/correlate.py). Files that cannot be read
are skipped and logged, so one bad workbook does not stop a correlation.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import FileRecord
from app.search.pgvector_retrieval import file_filter_conditions

logger = logging.getLogger(__name__)

TABLE_SUFFIXES = (".xlsx", ".xlsm", ".csv", ".tsv")
MAX_FILES = 30
MAX_ROWS = 5000
MIN_VALUES = 3  # a column needs at least this many numbers to take part


def read_numeric_tables(path: str) -> dict[str, pd.DataFrame]:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        sheets = pd.read_excel(p, sheet_name=None, engine="openpyxl")
    elif suffix == ".tsv":
        sheets = {p.name: pd.read_csv(p, sep="\t")}
    elif suffix == ".csv":
        sheets = {p.name: pd.read_csv(p)}
    else:
        return {}

    tables: dict[str, pd.DataFrame] = {}
    for sheet, frame in sheets.items():
        numeric = frame.head(MAX_ROWS).select_dtypes(include="number").dropna(axis=1, thresh=MIN_VALUES)
        if numeric.shape[1] >= 2:
            tables[f"{p.name} / {sheet}"] = numeric
    return tables


def load_numeric_tables(
    session: Session,
    raw_dir: str,
    folders: list[str] | None = None,
    file_ids: list[int] | None = None,
    max_files: int = MAX_FILES,
) -> dict[str, pd.DataFrame]:
    conditions = file_filter_conditions(raw_dir, folders, None, None, file_ids)
    paths = session.scalars(
        select(FileRecord.path)
        .where(*conditions, or_(*(FileRecord.path.ilike(f"%{s}") for s in TABLE_SUFFIXES)))
        .order_by(FileRecord.path)
        .limit(max_files)
    ).all()

    tables: dict[str, pd.DataFrame] = {}
    for path in paths:
        try:
            tables.update(read_numeric_tables(path))
        except Exception:  # a damaged or password-protected workbook is skipped, not fatal
            logger.warning("skipped numeric tables in %s", path, exc_info=True)
    return tables

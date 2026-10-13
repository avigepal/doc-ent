"""The change list of an edited Word file, kept next to the file so it can be
reviewed: every change the exact editor made (docx_ops.py) can be accepted or
rejected, and the file is rebuilt from the original with only the accepted ones.

`changes.json` beside the result holds the original file it was made from, the
operations (each change points at its operation), the style changes that were
applied on top, and which changes are currently accepted.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from docx import Document

from app.editing.docx_ops import Change, Op, apply_ops, read_blocks
from app.editing.docx_style import apply_ops as apply_style_ops
from app.editing.styling import StyleOp

CHANGES_NAME = "changes.json"
_VERSION = 1


def save_changes(
    result_path: Path,
    base: Path,
    ops: list[Op],
    changes: list[Change],
    style_ops: list[StyleOp] | None = None,
) -> None:
    """Remember the change list of `result_path` (all changes accepted)."""
    payload = {
        "version": _VERSION,
        "base": str(base),
        "ops": [asdict(op) for op in ops],
        "style_ops": [asdict(op) for op in style_ops or []],
        "changes": [asdict(c) for c in changes],
        "accepted": list(range(len(changes))),
    }
    (result_path.parent / CHANGES_NAME).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read(result_path: Path) -> dict | None:
    path = result_path.parent / CHANGES_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") == _VERSION else None


def describe_changes(result_path: Path) -> list[dict] | None:
    """The change list for the dashboard, or None when the file has none."""
    data = _read(result_path)
    if not data or not data.get("changes"):
        return None
    accepted = set(data.get("accepted") or [])
    return [
        {
            "index": i,
            "op": c["op"],
            "label": c["label"],
            "before": c["before"],
            "after": c["after"],
            "accepted": i in accepted,
        }
        for i, c in enumerate(data["changes"])
    ]


class ChangesUnavailable(Exception):
    """The original file, or the record of the changes, is gone."""


def apply_accepted(result_path: Path, accepted: list[int]) -> list[dict]:
    """Rebuild `result_path` from the original with only the accepted changes (and
    the style changes that were made with them), then return the new change list."""
    data = _read(result_path)
    if not data:
        raise ChangesUnavailable("this file has no change list")
    base = Path(data["base"])
    if not base.exists():
        raise ChangesUnavailable("the file these changes were made from is no longer on disk")

    count = len(data["changes"])
    keep = sorted({i for i in accepted if isinstance(i, int) and 0 <= i < count})
    wanted_ops = {data["changes"][i]["op_index"] for i in keep}
    ops = [Op(**raw) for index, raw in enumerate(data["ops"]) if index in wanted_ops]

    doc = Document(str(base))
    blocks = read_blocks(doc)
    apply_ops(doc, blocks, ops)
    style_ops = [StyleOp(**raw) for raw in data.get("style_ops") or []]
    if style_ops:
        apply_style_ops(doc, style_ops)

    tmp = result_path.with_name(f"{result_path.stem}.review.docx")
    doc.save(str(tmp))
    os.replace(tmp, result_path)

    data["accepted"] = keep
    (result_path.parent / CHANGES_NAME).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return describe_changes(result_path) or []

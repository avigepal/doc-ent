"""Ad-hoc export: turn a query result's text (built client-side as
Markdown) into a downloadable file on the spot, instead of requiring a
pre-saved summary file under summaries/. This is what makes "download"
an action on the query result itself rather than a separate page/step.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from app.export.pandoc_export import _default_runner, export_markdown


def safe_filename(name: str) -> str:
    """Strip anything that isn't alnum/underscore/hyphen so this can never
    escape the adhoc/ directory (no slashes, no '..', no path separators
    of any kind) — same intent as a slugify, but deliberately strict."""
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip())
    return name.strip("-") or "export"


def prepare_adhoc_export(
    content: str,
    filename: str,
    exports_root: Path,
    fmt: str = "pdf",
    runner: Callable[[list[str]], int] = _default_runner,
) -> Path:
    adhoc_dir = exports_root / "adhoc"
    adhoc_dir.mkdir(parents=True, exist_ok=True)

    source = adhoc_dir / f"{safe_filename(filename)}.md"
    source.write_text(content, encoding="utf-8")

    return export_markdown(source, adhoc_dir, fmt=fmt, runner=runner)

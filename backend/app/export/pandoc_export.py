"""Phase 5 — PDF-first export via pandoc.

PDF is the default/primary export format; .docx and .json stay available
as secondary options, same pandoc call with a different target (per your
answer: "PDF primary, others kept").

The actual subprocess call is injected as `runner` so this is unit
testable without pandoc/a LaTeX engine installed (tests fake the runner
and just assert the command built is correct) — the real runner is used
in production and by the Celery task.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

EXPORT_FORMATS = ("pdf", "docx", "json")

_PDF_ENGINE = "xelatex"  # installed via texlive-xetex in backend/Dockerfile; tectonic is a
# lighter-weight alternative but isn't a plain apt package on Debian

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# "Doc/Index" styled report assets (title block, colored section rules,
# citation-matching accent colors, footer) instead of pandoc's bare
# defaults — see report.latex/reference.docx for the actual styling.
_PDF_TEMPLATE = _TEMPLATES_DIR / "report.latex"
_REFERENCE_DOCX = _TEMPLATES_DIR / "reference.docx"


def _default_runner(cmd: list[str]) -> int:
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode


def export_markdown(
    source_md: Path,
    out_dir: Path,
    fmt: str = "pdf",
    runner: Callable[[list[str]], int] = _default_runner,
) -> Path:
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"unsupported export format: {fmt!r} (must be one of {EXPORT_FORMATS})")

    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / source_md.with_suffix(f".{fmt}").name

    cmd = ["pandoc", str(source_md), "-o", str(output_path)]
    if fmt == "pdf":
        cmd += ["--pdf-engine", _PDF_ENGINE, "--highlight-style", "tango"]
        if _PDF_TEMPLATE.exists():
            cmd += ["--template", str(_PDF_TEMPLATE)]
    elif fmt == "docx" and _REFERENCE_DOCX.exists():
        cmd += ["--reference-doc", str(_REFERENCE_DOCX)]

    returncode = runner(cmd)
    if returncode != 0:
        raise RuntimeError(f"pandoc exited with code {returncode} converting {source_md}")

    return output_path

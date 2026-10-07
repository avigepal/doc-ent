"""Phase 5 — PDF-first export via pandoc + weasyprint.

PDF is the default/primary export format; .docx and .json stay available
as secondary options (per your answer: "PDF primary, others kept").

PDF goes through two steps: pandoc converts markdown -> styled HTML (via
report.html, which supplies the "Doc/Index" title block, colored section
rules, chip-style citations and table styling), then weasyprint renders
that HTML -> PDF. CSS instead of a LaTeX template specifically because
chips/badges/colored boxes are native to CSS, and it reuses the same
color tokens as the live dashboard (dashboard/src/index.css) directly —
see report.html's own comment for the full reasoning.

Both subprocess calls are injected as `runner` so this is unit testable
without pandoc/weasyprint installed (tests fake the runner and just
assert the commands built are correct) — the real runner is used in
production and by the Celery task.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

EXPORT_FORMATS = ("pdf", "docx", "json")

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# "Doc/Index" styled report assets (title block, colored section rules,
# citation-matching accent colors, footer, table styling) instead of
# pandoc's/weasyprint's bare defaults — see report.html/reference.docx.
_PDF_HTML_TEMPLATE = _TEMPLATES_DIR / "report.html"
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

    if fmt == "pdf":
        html_path = out_dir / source_md.with_suffix(".html").name

        pandoc_cmd = ["pandoc", str(source_md), "-o", str(html_path), "--standalone"]
        if _PDF_HTML_TEMPLATE.exists():
            pandoc_cmd += ["--template", str(_PDF_HTML_TEMPLATE)]
        returncode = runner(pandoc_cmd)
        if returncode != 0:
            raise RuntimeError(f"pandoc exited with code {returncode} converting {source_md}")

        weasyprint_cmd = ["weasyprint", str(html_path), str(output_path)]
        returncode = runner(weasyprint_cmd)
        if returncode != 0:
            raise RuntimeError(f"weasyprint exited with code {returncode} rendering {html_path}")

        return output_path

    cmd = ["pandoc", str(source_md), "-o", str(output_path)]
    if fmt == "docx" and _REFERENCE_DOCX.exists():
        cmd += ["--reference-doc", str(_REFERENCE_DOCX)]

    returncode = runner(cmd)
    if returncode != 0:
        raise RuntimeError(f"pandoc exited with code {returncode} converting {source_md}")

    return output_path

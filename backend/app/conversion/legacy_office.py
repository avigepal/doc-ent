"""Old Microsoft Office files (.doc, .xls, .ppt) for Docling.

Docling reads only the XML formats (.docx, .xlsx, .pptx), so a legacy file is first
converted to its modern twin with LibreOffice running headless, and that copy goes to
Docling like any other document. The original is never touched: the conversion happens
on a copy in a temporary folder that is deleted afterwards.

LibreOffice is installed in the app image (see backend/Dockerfile).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.conversion.backends import ConversionBackend, ConversionResult

# legacy extension -> the modern format LibreOffice converts it to
LEGACY_TARGETS = {".doc": "docx", ".xls": "xlsx", ".ppt": "pptx"}

# what libmagic calls them -> the same modern format (decides when the extension is missing or wrong)
_MIME_TARGETS = {
    "application/msword": "docx",
    "application/vnd.ms-excel": "xlsx",
    "application/vnd.ms-powerpoint": "pptx",
}

# libmagic's name for an OLE file it can't tell apart: the extension decides
_GENERIC_OLE_MIMES = {"application/octet-stream", "application/x-ole-storage", "application/x-cfb", "application/CDFV2"}

# One file may take this long (seconds). Opening a big spreadsheet is slow; a hung
# LibreOffice must not hold a worker forever.
CONVERT_TIMEOUT_S = 120


class LegacyOfficeError(RuntimeError):
    pass


def legacy_target(mime_type: str, filename: str | None = None) -> str | None:
    """The modern format ("docx", "xlsx" or "pptx") a legacy Office file converts to, or None
    when this isn't a legacy Office file. Files that are really text or HTML under an old
    extension (an HTML page saved as .xls) are not legacy Office and return None."""
    if mime_type in _MIME_TARGETS:
        return _MIME_TARGETS[mime_type]
    if filename and mime_type in _GENERIC_OLE_MIMES:
        return LEGACY_TARGETS.get(Path(filename).suffix.lower())
    return None


def convert_with_libreoffice(source: Path, target: str, work_dir: Path, soffice: str = "soffice") -> Path:
    """Convert `source` to `target` ("docx", "xlsx" or "pptx") inside `work_dir`. Returns the new file."""
    out_dir = work_dir / "out"
    out_dir.mkdir()
    # a private profile per conversion: two LibreOffice processes sharing one profile block each other
    profile = work_dir / "profile"
    command = [
        soffice,
        "--headless",
        "--norestore",
        "--nolockcheck",
        f"-env:UserInstallation={profile.as_uri()}",
        "--convert-to",
        target,
        "--outdir",
        str(out_dir),
        str(source),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=CONVERT_TIMEOUT_S)
    except FileNotFoundError as exc:
        raise LegacyOfficeError(
            "LibreOffice is not installed in this image, which old .doc/.xls/.ppt files need"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise LegacyOfficeError(f"LibreOffice took longer than {CONVERT_TIMEOUT_S} seconds to convert the file") from exc

    converted = out_dir / f"{source.stem}.{target}"
    if completed.returncode != 0 or not converted.is_file():
        detail = (completed.stderr or completed.stdout or "").strip()[-300:]
        raise LegacyOfficeError(f"LibreOffice could not convert the file to .{target}" + (f": {detail}" if detail else ""))
    return converted


# the old extension for each modern format (the copy is named so LibreOffice picks the right importer)
_LEGACY_EXTENSIONS = {modern: legacy for legacy, modern in LEGACY_TARGETS.items()}


class LegacyOfficeBackend:
    """Converts a legacy Office file to its modern format, then lets `inner` (Docling) read it.
    `target` is the modern format when the caller already knows it (from the file's content);
    otherwise it comes from the extension."""

    def __init__(
        self, inner: ConversionBackend | None = None, soffice: str = "soffice", target: str | None = None
    ) -> None:
        self._inner = inner
        self._soffice = soffice
        self._target = target

    def _get_inner(self) -> ConversionBackend:
        if self._inner is None:
            from app.conversion.backends import docling_backend

            self._inner = docling_backend
        return self._inner

    def convert(self, path: Path) -> ConversionResult:
        target = self._target or LEGACY_TARGETS.get(path.suffix.lower())
        if target is None:
            raise LegacyOfficeError(f"{path.name}: not a .doc, .xls or .ppt file")

        with tempfile.TemporaryDirectory(prefix="legacy-office-") as tmp:
            work = Path(tmp)
            # work on a copy: the original stays exactly as it was
            copy = work / f"{path.stem}{_LEGACY_EXTENSIONS[target]}"
            shutil.copyfile(path, copy)
            modern = convert_with_libreoffice(copy, target, work, self._soffice)
            result = self._get_inner().convert(modern)

        return ConversionResult(
            markdown=result.markdown,
            engine=f"libreoffice+{result.engine}",
            engine_metadata={**result.engine_metadata, "title": path.stem},
        )

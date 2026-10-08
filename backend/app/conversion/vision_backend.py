"""Image conversion via the vision-capable llama-server model (the same
Qwen model that answers questions, started with its mmproj -- see
README/models.ini). One request per image returns both the text visible
in it (OCR-style transcription) and a description, written as Markdown
with `## ` headings so the normal chunker/indexer handle it like any
other converted document.

Scanned PDFs do not come through here: Docling OCRs those itself. This is
for standalone image files (PNG, JPEG, TIFF, BMP, WebP).
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path

from app.conversion.backends import ConversionResult

# Longest side sent to the model. Vision models downscale large inputs
# anyway; sending a 40MP photo only costs upload time and VRAM. 2048px
# keeps small print in screenshots and scans legible.
MAX_IMAGE_SIDE = 2048

# An image request can sit behind other requests on a single-slot server,
# or trigger a cold model load, so allow far longer than the 120s default.
VISION_TIMEOUT_SECONDS = 600.0

SYSTEM_PROMPT = (
    "You convert images into searchable Markdown for a document index. "
    "Be faithful to the image: never invent text that is not visible."
)

USER_PROMPT = (
    "Transcribe every piece of visible text exactly as written, keeping tables as Markdown tables. "
    "Then describe the image: what it shows, any charts or diagrams (axes, trends, key values), "
    "people or objects, and anything that would help someone find it by search.\n\n"
    "Reply in exactly this format:\n\n"
    "## Text in image\n<the transcription, or 'None' if there is no readable text>\n\n"
    "## Description\n<the description>"
)

_HEADING_RE = re.compile(r"^## ", re.MULTILINE)


def prepare_image_data_url(path: Path, max_side: int = MAX_IMAGE_SIDE) -> tuple[str, tuple[int, int]]:
    """Open, orient, downscale and JPEG-encode an image as a base64 data
    URL. Returns (data_url, (original_width, original_height))."""
    from PIL import Image, ImageOps

    with Image.open(path) as img:
        img.load()  # first frame of multi-page TIFFs; surfaces corrupt files here
        original_size = img.size
        img = ImageOps.exif_transpose(img)
        # JPEG has no alpha/palette: flatten onto white so transparent
        # screenshots don't turn black.
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
        img.thumbnail((max_side, max_side))

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=90)

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}", original_size


def shape_markdown(model_output: str) -> str:
    """Guarantee `## ` section headings so the chunker has sections to
    split on, even when the model ignores the requested format."""
    text = model_output.strip()
    if not text:
        raise ValueError("vision model returned no content")
    if _HEADING_RE.search(text):
        return text
    return f"## Description\n\n{text}"


class VisionBackend:
    def __init__(self, llm=None) -> None:
        self._llm = llm

    def _get_llm(self):
        if self._llm is None:
            from app.config import settings
            from app.summarization.llm_client import LlamaClient

            self._llm = LlamaClient(
                base_url=settings.llama_vision_url,
                model=settings.llama_vision_model,
                api_key=settings.llama_vision_api_key or None,
                timeout=VISION_TIMEOUT_SECONDS,
            )
        return self._llm

    def convert(self, path: Path) -> ConversionResult:
        data_url, (width, height) = prepare_image_data_url(path)
        output = self._get_llm().chat_with_image(SYSTEM_PROMPT, USER_PROMPT, data_url)
        return ConversionResult(
            markdown=shape_markdown(output),
            engine="vision",
            engine_metadata={"title": path.stem, "width": width, "height": height},
        )


vision_backend = VisionBackend()

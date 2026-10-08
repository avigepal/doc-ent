import base64
import io
import json

import httpx
import pytest
from PIL import Image

from app.conversion.backends import resolve_conversion_backend
from app.conversion.vision_backend import (
    MAX_IMAGE_SIDE,
    VisionBackend,
    prepare_image_data_url,
    shape_markdown,
)
from app.summarization.llm_client import LlamaClient


def _decode(data_url: str) -> Image.Image:
    assert data_url.startswith("data:image/jpeg;base64,")
    return Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))


def test_prepare_downscales_large_images_and_reports_original_size(tmp_path):
    path = tmp_path / "big.png"
    Image.new("RGB", (4000, 1000), "white").save(path)

    data_url, original = prepare_image_data_url(path)

    assert original == (4000, 1000)
    sent = _decode(data_url)
    assert max(sent.size) == MAX_IMAGE_SIDE
    assert sent.size == (2048, 512)  # aspect ratio kept


def test_prepare_leaves_small_images_alone(tmp_path):
    path = tmp_path / "small.png"
    Image.new("RGB", (300, 200), "white").save(path)

    data_url, _ = prepare_image_data_url(path)

    assert _decode(data_url).size == (300, 200)


def test_prepare_flattens_transparency_onto_white(tmp_path):
    path = tmp_path / "transparent.png"
    Image.new("RGBA", (50, 50), (0, 0, 0, 0)).save(path)

    sent = _decode(prepare_image_data_url(path)[0])

    r, g, b = sent.getpixel((25, 25))
    assert min(r, g, b) > 240  # white, not black


def test_prepare_raises_on_corrupt_image(tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"this is not an image")

    with pytest.raises(Exception):
        prepare_image_data_url(path)


def test_shape_markdown_keeps_model_headings():
    out = "## Text in image\nHello\n\n## Description\nA sign."
    assert shape_markdown(out) == out


def test_shape_markdown_adds_heading_when_model_ignores_format():
    assert shape_markdown("  A photo of a cat.  ") == "## Description\n\nA photo of a cat."


def test_shape_markdown_rejects_empty_output():
    with pytest.raises(ValueError):
        shape_markdown("   \n")


class _FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat_with_image(self, system, user_text, image_data_url, **_):
        self.calls.append((system, user_text, image_data_url))
        return self.reply


def test_convert_returns_markdown_with_image_metadata(tmp_path):
    path = tmp_path / "receipt.png"
    Image.new("RGB", (640, 480), "white").save(path)
    llm = _FakeLLM("## Text in image\nTotal: $4\n\n## Description\nA receipt.")

    result = VisionBackend(llm=llm).convert(path)

    assert result.engine == "vision"
    assert "Total: $4" in result.markdown
    assert result.engine_metadata == {"title": "receipt", "width": 640, "height": 480}
    assert llm.calls[0][2].startswith("data:image/jpeg;base64,")


def test_resolve_backend_sends_images_to_vision_and_not_other_types():
    assert isinstance(resolve_conversion_backend("image/png"), VisionBackend)
    assert isinstance(resolve_conversion_backend("image/jpeg"), VisionBackend)
    assert not isinstance(resolve_conversion_backend("application/pdf"), VisionBackend)


def test_chat_with_image_sends_openai_image_content_part():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = LlamaClient(
        base_url="http://llama:8080",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        model="qwen-vl",
        api_key="secret",
    )

    assert client.chat_with_image("sys", "look", "data:image/jpeg;base64,AAA", max_tokens=50) == "ok"

    body = captured["body"]
    assert body["model"] == "qwen-vl"
    assert body["max_tokens"] == 50
    parts = body["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "look"}
    assert parts[1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}}
    assert captured["auth"] == "Bearer secret"

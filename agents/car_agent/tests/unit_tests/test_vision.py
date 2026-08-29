"""图像识别接口、输入边界和工具输出测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.tools.vision import recognize_image
from agent.vision.factory import build_vision_recognizer
from agent.vision.recognizer import (
    DEFAULT_QUESTION,
    DefaultVisionRecognizer,
    ProviderResponse,
    VisionRecognitionError,
    VisionResult,
)

pytestmark = pytest.mark.anyio
FIXTURE = Path("/home/lk/car/test/fixtures/esp_vga_q20.jpg")


class FakeVisionProvider:
    """记录调用参数并返回固定答案的异步 Provider。"""

    def __init__(self, answer: str = "一张乡村场景图片。") -> None:
        self.answer = answer
        self.calls: list[tuple[bytes, str, str]] = []

    async def recognize(
        self,
        image_data: bytes,
        media_type: str,
        question: str,
    ) -> ProviderResponse:
        self.calls.append((image_data, media_type, question))
        return ProviderResponse(
            answer=self.answer,
            provider="fake",
            model="fake-vision",
        )


async def test_recognizer_normalizes_default_question_and_result() -> None:
    provider = FakeVisionProvider()
    recognizer = DefaultVisionRecognizer(provider, (FIXTURE.parent,))

    result = await recognizer.recognize(FIXTURE)

    assert result.status == "success"
    assert result.answer == "一张乡村场景图片。"
    assert result.provider == "fake"
    assert result.model == "fake-vision"
    assert result.latency_ms is not None
    assert provider.calls[0][1] == "image/jpeg"
    assert provider.calls[0][2] == DEFAULT_QUESTION


async def test_recognizer_passes_custom_question_and_strips_answer() -> None:
    provider = FakeVisionProvider("  这是一辆车。\n")
    recognizer = DefaultVisionRecognizer(provider, (FIXTURE.parent,))

    result = await recognizer.recognize(FIXTURE, "这个是什么？")

    assert result.answer == "这是一辆车。"
    assert provider.calls[0][2] == "这个是什么？"


async def test_recognizer_rejects_path_outside_allowlist(tmp_path: Path) -> None:
    image = tmp_path / "outside.jpg"
    image.write_bytes(FIXTURE.read_bytes())
    recognizer = DefaultVisionRecognizer(
        provider=FakeVisionProvider(), allowed_roots=(FIXTURE.parent,)
    )

    with pytest.raises(VisionRecognitionError, match="不在允许") as raised:
        await recognizer.recognize(image)
    assert raised.value.code == "IMAGE_PATH_NOT_ALLOWED"


async def test_recognizer_rejects_invalid_image(tmp_path: Path) -> None:
    image = tmp_path / "invalid.jpg"
    image.write_bytes(b"not an image")
    recognizer = DefaultVisionRecognizer(FakeVisionProvider(), (tmp_path,))

    with pytest.raises(VisionRecognitionError) as raised:
        await recognizer.recognize(image)
    assert raised.value.code == "INVALID_IMAGE"


async def test_recognizer_rejects_long_question() -> None:
    provider = FakeVisionProvider()
    recognizer = DefaultVisionRecognizer(provider, (FIXTURE.parent,))

    with pytest.raises(VisionRecognitionError) as raised:
        await recognizer.recognize(FIXTURE, "x" * 101)
    assert raised.value.code == "INVALID_QUESTION"
    assert provider.calls == []


def test_factory_selects_supported_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_ALLOWED_IMAGE_DIRS", str(FIXTURE.parent))
    recognizer = build_vision_recognizer("ollama")

    assert isinstance(recognizer, DefaultVisionRecognizer)


def test_factory_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="不支持的"):
        build_vision_recognizer("unknown")


async def test_image_tool_returns_normalized_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRecognizer:
        async def recognize(
            self, image_path: Path, question: str | None = None
        ) -> VisionResult:
            assert str(image_path) == str(FIXTURE)
            assert question == "这个是什么？"
            return VisionResult(
                status="success",
                answer="一辆车。",
                provider="fake",
                model="fake-vision",
                latency_ms=1.2,
            )

    monkeypatch.setattr(
        "agent.tools.vision.get_vision_recognizer", lambda: FakeRecognizer()
    )
    result = await recognize_image.ainvoke(
        {"image_path": str(FIXTURE), "question": "这个是什么？"}
    )

    assert result["status"] == "success"
    assert result["answer"] == "一辆车。"
    assert result["latency_ms"] == 1.2

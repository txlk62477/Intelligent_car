"""图像识别的稳定接口、输入校验和统一结果模型。"""

from __future__ import annotations

import asyncio
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

DEFAULT_QUESTION = "请简短描述图片中的主要内容。"
SUPPORTED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class VisionRecognizer(Protocol):
    """所有图像识别 Provider 对调用方提供的统一接口。"""

    async def recognize(
        self,
        image_path: Path,
        question: str | None = None,
    ) -> VisionResult:
        """识别一张本地图片，可选地回答一个问题。"""


class VisionProvider(Protocol):
    """Provider 适配器的内部接口；适配器不负责本地文件校验。"""

    async def recognize(
        self,
        image_data: bytes,
        media_type: str,
        question: str,
    ) -> ProviderResponse:
        """向远端或本地视觉模型发送已校验的图片。"""


@dataclass(frozen=True)
class ProviderResponse:
    """Provider 返回的最小答案。"""

    answer: str
    provider: str
    model: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VisionResult:
    """Agent 工具对所有 Provider 暴露的统一结果。"""

    status: str
    answer: str | None = None
    provider: str | None = None
    model: str | None = None
    latency_ms: float | None = None
    error_code: str | None = None
    message: str | None = None
    retryable: bool = False

    @classmethod
    def success(
        cls,
        response: ProviderResponse,
        latency_ms: float,
    ) -> VisionResult:
        """创建成功结果，并复制 Provider 的诊断元数据之外的公共字段。"""
        return cls(
            status="success",
            answer=response.answer,
            provider=response.provider,
            model=response.model,
            latency_ms=round(latency_ms, 1),
        )

    @classmethod
    def failure(cls, error: VisionRecognitionError) -> VisionResult:
        """把领域异常转换为可安全返回给 Agent 的结构化结果。"""
        return cls(
            status="failed",
            error_code=error.code,
            message=error.message,
            retryable=error.retryable,
        )

    def as_dict(self) -> dict[str, Any]:
        """转换成工具和 API 都可以直接序列化的字典。"""
        result: dict[str, Any] = {"status": self.status}
        if self.status == "success":
            result.update(
                {
                    "answer": self.answer,
                    "provider": self.provider,
                    "model": self.model,
                    "latency_ms": self.latency_ms,
                }
            )
        else:
            result.update(
                {
                    "error_code": self.error_code,
                    "message": self.message,
                    "retryable": self.retryable,
                }
            )
        return result


class VisionRecognitionError(Exception):
    """可预期的图像识别错误；不会携带图片、Base64 或密钥。"""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        """保存安全的错误码、用户消息和是否适合重试。"""
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class DefaultVisionRecognizer:
    """负责输入边界和结果规范化的深模块。"""

    def __init__(
        self,
        provider: VisionProvider,
        allowed_roots: tuple[Path, ...],
        *,
        default_question: str = DEFAULT_QUESTION,
        max_image_bytes: int = 10 * 1024 * 1024,
        min_dimension: int = 64,
        max_dimension: int = 4096,
    ) -> None:
        """配置 Provider、允许目录和图片边界。"""
        self._provider = provider
        self._allowed_roots = tuple(root.resolve() for root in allowed_roots)
        self._default_question = default_question.strip() or DEFAULT_QUESTION
        self._max_image_bytes = max_image_bytes
        self._min_dimension = min_dimension
        self._max_dimension = max_dimension

    async def recognize(
        self,
        image_path: Path,
        question: str | None = None,
    ) -> VisionResult:
        """校验图片、调用 Provider，并测量一次完整识别的端到端延迟。"""
        started = time.perf_counter()
        try:
            # 文件校验和读取（resolve/stat/read_bytes）移到后台线程，避免阻塞事件循环。
            image_data, media_type = await asyncio.to_thread(
                self._load_image, image_path
            )
            normalized_question = self._normalize_question(question)
            response = await self._provider.recognize(
                image_data,
                media_type,
                normalized_question,
            )
            answer = response.answer.strip()
            if not answer:
                raise VisionRecognitionError(
                    "EMPTY_ANSWER", "图像识别服务返回了空答案。", retryable=True
                )
            return VisionResult.success(
                ProviderResponse(
                    answer=answer,
                    provider=response.provider,
                    model=response.model,
                    metadata=response.metadata,
                ),
                (time.perf_counter() - started) * 1000,
            )
        except VisionRecognitionError:
            raise
        except (OSError, ValueError) as error:
            raise VisionRecognitionError(
                "IMAGE_READ_ERROR", f"无法读取图片：{error}"
            ) from error
        except Exception as error:
            raise VisionRecognitionError(
                "INTERNAL_ERROR", "图像识别模块发生内部错误。"
            ) from error

    def _normalize_question(self, question: str | None) -> str:
        normalized = (question or self._default_question).strip()
        if not normalized:
            normalized = DEFAULT_QUESTION
        if len(normalized) > 100:
            raise VisionRecognitionError(
                "INVALID_QUESTION", "问题长度不能超过 100 个字符。"
            )
        return normalized

    def _load_image(self, image_path: Path) -> tuple[bytes, str]:
        candidate = Path(image_path).expanduser()
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise VisionRecognitionError(
                "IMAGE_NOT_FOUND", "图片文件不存在。"
            ) from error
        if not resolved.is_file():
            raise VisionRecognitionError("IMAGE_NOT_FOUND", "图片路径不是普通文件。")
        if not any(_is_relative_to(resolved, root) for root in self._allowed_roots):
            raise VisionRecognitionError(
                "IMAGE_PATH_NOT_ALLOWED", "图片路径不在允许的图片目录中。"
            )
        try:
            size = resolved.stat().st_size
        except OSError as error:
            raise VisionRecognitionError(
                "IMAGE_READ_ERROR", "无法读取图片文件信息。"
            ) from error
        if size > self._max_image_bytes:
            raise VisionRecognitionError(
                "IMAGE_TOO_LARGE",
                f"图片文件不能超过 {self._max_image_bytes} 字节。",
            )

        try:
            data = resolved.read_bytes()
            image_format, width, height = _image_info(data)
            media_type = SUPPORTED_FORMATS[image_format]
            if min(width, height) < self._min_dimension:
                raise VisionRecognitionError(
                    "IMAGE_TOO_SMALL",
                    f"图片最短边不能小于 {self._min_dimension}px。",
                )
            if max(width, height) > self._max_dimension:
                raise VisionRecognitionError(
                    "IMAGE_TOO_LARGE",
                    f"图片最长边不能超过 {self._max_dimension}px。",
                )
        except VisionRecognitionError:
            raise
        except (OSError, ValueError, struct.error) as error:
            raise VisionRecognitionError(
                "INVALID_IMAGE", "图片格式无效或内容已损坏。"
            ) from error
        return data, media_type


def _image_info(data: bytes) -> tuple[str, int, int]:
    """用标准库检查三种常见格式的签名、尺寸和基本结构。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 33 or data[12:16] != b"IHDR":
            raise ValueError("PNG IHDR 不完整")
        width, height = struct.unpack(">II", data[16:24])
        if width == 0 or height == 0:
            raise ValueError("PNG 尺寸无效")
        return "PNG", width, height
    if data.startswith(b"RIFF") and len(data) >= 30 and data[8:12] == b"WEBP":
        return _webp_info(data)
    if data.startswith(b"\xff\xd8"):
        return _jpeg_info(data)
    raise ValueError("不支持的图片格式")


def _jpeg_info(data: bytes) -> tuple[str, int, int]:
    """读取 JPEG SOF 尺寸并检查段边界。"""
    sof_markers = (
        set(range(0xC0, 0xC4))
        | set(range(0xC5, 0xC8))
        | set(range(0xC9, 0xCC))
        | set(range(0xCD, 0xD0))
    )
    index = 2
    while index < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker == 0xDA:
            break
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            raise ValueError("JPEG 段长度不完整")
        segment_length = struct.unpack(">H", data[index : index + 2])[0]
        if segment_length < 2 or index + segment_length > len(data):
            raise ValueError("JPEG 段边界无效")
        if marker in sof_markers:
            if segment_length < 7:
                raise ValueError("JPEG SOF 不完整")
            height, width = struct.unpack(">HH", data[index + 3 : index + 7])
            if width == 0 or height == 0:
                raise ValueError("JPEG 尺寸无效")
            return "JPEG", width, height
        index += segment_length
    raise ValueError("JPEG 尺寸无法确定")


def _webp_info(data: bytes) -> tuple[str, int, int]:
    """读取 WebP 的 VP8、VP8L 或 VP8X 尺寸。"""
    chunk = data[12:16]
    if chunk == b"VP8X":
        if len(data) < 30:
            raise ValueError("WEBP VP8X 不完整")
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return "WEBP", width, height
    if chunk == b"VP8 " and len(data) >= 30:
        start = 20
        if data[start + 3 : start + 6] != b"\x9d\x01\x2a":
            raise ValueError("WEBP VP8 帧头无效")
        width, height = struct.unpack("<HH", data[start + 6 : start + 10])
        return "WEBP", width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        width = 1 + (bits & 0x3FFF)
        height = 1 + ((bits >> 14) & 0x3FFF)
        return "WEBP", width, height
    raise ValueError("WEBP 帧格式不支持")


def _is_relative_to(path: Path, root: Path) -> bool:
    """兼容 Path.is_relative_to，同时保持路径边界精确。"""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = [
    "DEFAULT_QUESTION",
    "DefaultVisionRecognizer",
    "ProviderResponse",
    "VisionProvider",
    "VisionRecognitionError",
    "VisionRecognizer",
    "VisionResult",
]

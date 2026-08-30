"""根据环境配置构建图像识别 Provider。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from agent.vision.adapters.baidu import BaiduVisionAdapter
from agent.vision.adapters.ollama import OllamaVisionAdapter
from agent.vision.recognizer import (
    DEFAULT_QUESTION,
    DefaultVisionRecognizer,
    VisionProvider,
    VisionRecognizer,
)


def build_vision_recognizer(provider: str | None = None) -> VisionRecognizer:
    """按显式参数或 `VISION_PROVIDER` 构建一个图像识别器。"""
    provider_name = (provider or os.getenv("VISION_PROVIDER") or "ollama").strip().lower()
    allowed_roots = _allowed_roots()
    adapter: VisionProvider
    if provider_name == "ollama":
        adapter = OllamaVisionAdapter(
            base_url=os.getenv(
                "OLLAMA_VISION_URL",
                os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
            ),
            model=os.getenv("OLLAMA_VISION_MODEL", "qwen3-vl:4b-instruct"),
            timeout=_float_env("OLLAMA_VISION_TIMEOUT", 120.0),
            keep_alive=os.getenv("OLLAMA_VISION_KEEP_ALIVE", "30m"),
            num_predict=_int_env("OLLAMA_VISION_NUM_PREDICT", 64),
        )
    elif provider_name == "baidu":
        api_key = os.getenv("BAIDU_API_KEY", "").strip()
        secret_key = os.getenv("BAIDU_SECRET_KEY", "").strip()
        if not api_key or not secret_key:
            raise RuntimeError(
                "VISION_PROVIDER=baidu 需要配置 BAIDU_API_KEY 和 BAIDU_SECRET_KEY。"
            )
        adapter = BaiduVisionAdapter(
            api_key=api_key,
            secret_key=secret_key,
            base_url=os.getenv("BAIDU_BASE_URL", "https://aip.baidubce.com"),
            poll_interval=_float_env("BAIDU_VISION_POLL_INTERVAL", 0.3),
            timeout=_float_env("BAIDU_VISION_TIMEOUT", 30.0),
            max_retries=_int_env("BAIDU_VISION_MAX_RETRIES", 1),
        )
    else:
        raise ValueError(
            f"不支持的 VISION_PROVIDER={provider_name!r}，可选值为 ollama 或 baidu。"
        )
    return DefaultVisionRecognizer(
        adapter,
        allowed_roots,
        default_question=os.getenv("VISION_DEFAULT_QUESTION", DEFAULT_QUESTION),
        max_image_bytes=_int_env("VISION_MAX_IMAGE_BYTES", 10 * 1024 * 1024),
    )


@lru_cache(maxsize=1)
def get_vision_recognizer() -> VisionRecognizer:
    """获取进程内共享的图像识别器；测试时可清理此缓存。"""
    return build_vision_recognizer()


def _allowed_roots() -> tuple[Path, ...]:
    configured = os.getenv("VISION_ALLOWED_IMAGE_DIRS", "").strip()
    if configured:
        roots = tuple(Path(item).expanduser() for item in configured.split(os.pathsep) if item)
    else:
        roots = (_DEFAULT_IMAGE_ROOT,)
    return roots


# 默认只允许项目工作目录下的 images，避免模型获得任意文件读取能力。
# 在导入期计算一次，避免在事件循环里触发阻塞的 os.getcwd()。
_DEFAULT_IMAGE_ROOT: Path = Path.cwd() / "images"


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{name} 必须是整数。") from error
    if parsed <= 0:
        raise ValueError(f"{name} 必须大于 0。")
    return parsed


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{name} 必须是数字。") from error
    if parsed <= 0:
        raise ValueError(f"{name} 必须大于 0。")
    return parsed


__all__ = ["build_vision_recognizer", "get_vision_recognizer"]

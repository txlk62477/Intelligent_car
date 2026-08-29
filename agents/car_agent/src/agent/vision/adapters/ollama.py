"""Ollama 本地视觉模型适配器。"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any

from agent.vision.recognizer import ProviderResponse, VisionRecognitionError


class OllamaVisionAdapter:
    """通过 Ollama 的同步 `/api/generate` 接口调用视觉模型。"""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        keep_alive: str | int = "30m",
        num_predict: int = 64,
    ) -> None:
        """保存 Ollama 地址、模型和生成参数。"""
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._keep_alive = keep_alive
        self._num_predict = num_predict

    async def recognize(
        self,
        image_data: bytes,
        media_type: str,
        question: str,
    ) -> ProviderResponse:
        """执行一次阻塞 HTTP 请求并返回统一 Provider 响应。"""
        return self._recognize_sync(image_data, media_type, question)

    def _recognize_sync(
        self,
        image_data: bytes,
        media_type: str,
        question: str,
    ) -> ProviderResponse:
        del media_type  # Ollama 当前只需要图片 Base64，格式由图片内容识别。
        payload = {
            "model": self._model,
            "prompt": question,
            "images": [base64.b64encode(image_data).decode("ascii")],
            "stream": False,
            "think": False,
            "keep_alive": self._keep_alive,
            "options": {"temperature": 0, "num_predict": self._num_predict},
        }
        result = _request_json(
            f"{self._base_url}/api/generate",
            payload,
            timeout=self._timeout,
            provider="Ollama",
        )
        if result.get("done") is not True:
            raise VisionRecognitionError(
                "PROVIDER_INCOMPLETE", "Ollama 未完成图像识别。", retryable=True
            )
        answer = result.get("response")
        if not isinstance(answer, str) or not answer.strip():
            raise VisionRecognitionError(
                "EMPTY_ANSWER", "Ollama 返回了空答案。", retryable=True
            )
        return ProviderResponse(
            answer=answer,
            provider="ollama",
            model=self._model,
            metadata={
                "api_total_ms": _duration_ms(result.get("total_duration")),
                "load_ms": _duration_ms(result.get("load_duration")),
                "prompt_eval_ms": _duration_ms(result.get("prompt_eval_duration")),
                "eval_ms": _duration_ms(result.get("eval_duration")),
                "prompt_tokens": result.get("prompt_eval_count"),
                "output_tokens": result.get("eval_count"),
            },
        )


def _request_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float,
    provider: str,
) -> dict[str, Any]:
    """执行 JSON POST，并将网络错误转换为不泄露请求内容的领域错误。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        try:
            error.read(500)
        except OSError:
            pass
        raise VisionRecognitionError(
            f"{provider.upper()}_HTTP_{error.code}",
            f"{provider} 服务返回 HTTP {error.code}。",
            retryable=error.code >= 500,
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise VisionRecognitionError(
            "PROVIDER_UNAVAILABLE",
            f"无法连接 {provider} 图像识别服务。",
            retryable=True,
        ) from error
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VisionRecognitionError(
            "PROVIDER_INVALID_RESPONSE",
            f"{provider} 返回了无效 JSON。",
            retryable=True,
        ) from error
    if not isinstance(result, dict):
        raise VisionRecognitionError(
            "PROVIDER_INVALID_RESPONSE",
            f"{provider} 返回了非对象 JSON。",
        )
    if result.get("error"):
        raise VisionRecognitionError(
            "PROVIDER_ERROR",
            f"{provider} 图像识别失败。",
            retryable=True,
        )
    return result


def _duration_ms(value: Any) -> float | None:
    """转换 Ollama 以纳秒返回的诊断字段。"""
    if isinstance(value, (int, float)):
        return round(value / 1_000_000, 1)
    return None


__all__ = ["OllamaVisionAdapter"]

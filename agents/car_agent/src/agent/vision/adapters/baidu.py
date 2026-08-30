"""百度云图像内容理解异步 API 适配器。"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from agent.vision.recognizer import ProviderResponse, VisionRecognitionError


class BaiduVisionAdapter:
    """封装百度 Token、提交任务和轮询过程，保持与 Ollama 相同的接口。"""

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        base_url: str = "https://aip.baidubce.com",
        poll_interval: float = 0.3,
        timeout: float = 30.0,
        max_retries: int = 1,
    ) -> None:
        """保存百度认证、轮询和重试参数；Token 在首次调用时获取。"""
        self._api_key = api_key
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._max_retries = max_retries
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    async def recognize(
        self,
        image_data: bytes,
        media_type: str,
        question: str,
    ) -> ProviderResponse:
        """执行百度的阻塞式提交与轮询流程。"""
        return self._recognize_sync(image_data, media_type, question)

    def _recognize_sync(
        self,
        image_data: bytes,
        media_type: str,
        question: str,
    ) -> ProviderResponse:
        del media_type  # 百度根据 Base64 内容识别图片格式。
        encoded = base64.b64encode(image_data).decode("ascii")
        last_error: VisionRecognitionError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                answer = self._recognize_once(encoded, question)
                return ProviderResponse(
                    answer=answer,
                    provider="baidu",
                    model="image-understanding",
                )
            except _RetryBaiduError as error:
                last_error = error
                if attempt >= self._max_retries:
                    break
                if error.invalidate_token:
                    self._invalidate_token()
        if last_error is not None:
            raise last_error
        raise VisionRecognitionError(
            "BAIDU_ERROR", "百度图像识别失败。", retryable=True
        )

    def _recognize_once(self, image_b64: str, question: str) -> str:
        token = self._get_access_token()
        submit_url = self._with_token(
            "/rest/2.0/image-classify/v1/image-understanding/request", token
        )
        submitted = _request_json(
            submit_url,
            {"image": image_b64, "question": question},
            timeout=self._timeout,
        )
        task_id = submitted.get("result", {}).get("task_id")
        if not task_id:
            raise _classify_baidu_error(submitted, "提交图像理解任务失败。")

        deadline = time.monotonic() + self._timeout
        result_url = self._with_token(
            "/rest/2.0/image-classify/v1/image-understanding/get-result", token
        )
        while True:
            if time.monotonic() > deadline:
                raise VisionRecognitionError(
                    "PROVIDER_TIMEOUT", "百度图像识别处理超时。", retryable=True
                )
            result = _request_json(
                result_url,
                {"task_id": str(task_id)},
                timeout=self._timeout,
            )
            details = result.get("result", {})
            try:
                ret_code = int(details.get("ret_code", -1))
            except (TypeError, ValueError):
                ret_code = -1
            if ret_code == 0:
                description = details.get("description")
                if isinstance(description, str) and description.strip():
                    return description.strip()
                raise VisionRecognitionError(
                    "EMPTY_ANSWER", "百度返回了空答案。", retryable=True
                )
            if ret_code == 1:
                time.sleep(self._poll_interval)
                continue
            raise _classify_baidu_error(result, "百度图像识别失败。")

    def _get_access_token(self) -> str:
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token
        with self._token_lock:
            now = time.monotonic()
            if self._token and now < self._token_expires_at:
                return self._token
            query = urllib.parse.urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": self._api_key,
                    "client_secret": self._secret_key,
                }
            )
            result = _request_json(
                f"{self._base_url}/oauth/2.0/token?{query}",
                None,
                timeout=self._timeout,
            )
            token = result.get("access_token")
            if not isinstance(token, str) or not token:
                raise _classify_baidu_error(result, "百度 Access Token 获取失败。")
            try:
                expires_in = max(float(result.get("expires_in", 1800)), 60.0)
            except (TypeError, ValueError):
                expires_in = 1800.0
            self._token = token
            self._token_expires_at = time.monotonic() + expires_in - 30.0
            return token

    def _invalidate_token(self) -> None:
        with self._token_lock:
            self._token = None
            self._token_expires_at = 0.0

    def _with_token(self, path: str, token: str) -> str:
        encoded_token = urllib.parse.quote(token, safe="")
        return f"{self._base_url}{path}?access_token={encoded_token}"


class _RetryBaiduError(VisionRecognitionError):
    def __init__(
        self, code: str, message: str, *, invalidate_token: bool = False
    ) -> None:
        super().__init__(code, message, retryable=True)
        self.invalidate_token = invalidate_token


def _request_json(
    url: str,
    payload: dict[str, Any] | None,
    *,
    timeout: float,
) -> dict[str, Any]:
    body = (
        None
        if payload is None
        else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        try:
            error.read(500)
        except OSError:
            pass
        raise VisionRecognitionError(
            f"BAIDU_HTTP_{error.code}",
            f"百度服务返回 HTTP {error.code}。",
            retryable=error.code >= 500,
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise VisionRecognitionError(
            "PROVIDER_UNAVAILABLE",
            "无法连接百度图像识别服务。",
            retryable=True,
        ) from error
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VisionRecognitionError(
            "PROVIDER_INVALID_RESPONSE", "百度返回了无效 JSON。", retryable=True
        ) from error
    if not isinstance(result, dict):
        raise VisionRecognitionError(
            "PROVIDER_INVALID_RESPONSE", "百度返回了非对象 JSON。"
        )
    return result


def _classify_baidu_error(
    result: dict[str, Any], fallback: str
) -> VisionRecognitionError:
    """识别百度错误码；216630 等临时错误只进行有限重试。"""
    details = result.get("result")
    details = details if isinstance(details, dict) else {}
    raw_code = details.get(
        "error_code", result.get("error_code", details.get("ret_code"))
    )
    try:
        code = int(raw_code) if raw_code is not None else 0
    except (TypeError, ValueError):
        code = 0
    message = (
        details.get("error_msg") or details.get("ret_msg") or result.get("error_msg")
    )
    safe_message = str(message) if message else fallback
    if code in {110, 111}:
        return _RetryBaiduError(f"BAIDU_{code}", safe_message, invalidate_token=True)
    if code in {216630, 216634, 18, 282000}:
        return _RetryBaiduError(f"BAIDU_{code}", safe_message)
    return VisionRecognitionError(
        f"BAIDU_{code}" if code else "BAIDU_ERROR",
        safe_message,
        retryable=False,
    )


__all__ = ["BaiduVisionAdapter"]

"""Ollama 与百度适配器的协议测试，不访问真实网络。"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

import agent.vision.adapters.baidu as baidu_module
import agent.vision.adapters.ollama as ollama_module
from agent.vision.adapters.baidu import BaiduVisionAdapter
from agent.vision.adapters.ollama import OllamaVisionAdapter

pytestmark = pytest.mark.anyio


class FakeHTTPResponse:
    """满足 urllib 上下文管理器协议的内存响应。"""

    def __init__(self, value: dict[str, Any]) -> None:
        self.raw = json.dumps(value, ensure_ascii=False).encode()

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, amount: int = -1) -> bytes:
        return self.raw[:amount] if amount >= 0 else self.raw


async def test_ollama_sends_raw_base64_and_generation_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, dict[str, Any]]] = []

    def fake_urlopen(request: Any, timeout: float) -> FakeHTTPResponse:
        requests.append((request.full_url, json.loads(request.data)))
        return FakeHTTPResponse({"done": True, "response": "这是一张图。"})

    monkeypatch.setattr(ollama_module.urllib.request, "urlopen", fake_urlopen)
    adapter = OllamaVisionAdapter(
        base_url="http://ollama.test",
        model="qwen3-vl:4b-instruct",
        timeout=3,
    )

    result = await adapter.recognize(b"fake-image", "image/jpeg", "这个是什么？")

    assert result.answer == "这是一张图。"
    assert requests[0][0] == "http://ollama.test/api/generate"
    payload = requests[0][1]
    assert payload["images"] == [base64.b64encode(b"fake-image").decode()]
    assert payload["prompt"] == "这个是什么？"
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["options"] == {"temperature": 0, "num_predict": 64}


async def test_baidu_submits_raw_base64_then_polls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any] | None]] = []
    responses = iter(
        [
            {"result": {"task_id": "task-1"}},
            {"result": {"ret_code": 1, "ret_msg": "processing"}},
            {"result": {"ret_code": 0, "description": "乡村场景。"}},
        ]
    )

    def fake_request(
        url: str, payload: dict[str, Any] | None, *, timeout: float
    ) -> dict[str, Any]:
        calls.append((url, payload))
        return next(responses)

    adapter = BaiduVisionAdapter(
        api_key="api-key",
        secret_key="secret-key",
        poll_interval=0.001,
        timeout=2,
    )
    monkeypatch.setattr(adapter, "_get_access_token", lambda: "access-token")
    monkeypatch.setattr(baidu_module, "_request_json", fake_request)

    result = await adapter.recognize(b"fake-image", "image/jpeg", "这个是什么？")

    assert result.answer == "乡村场景。"
    assert calls[0][1] == {
        "image": base64.b64encode(b"fake-image").decode("ascii"),
        "question": "这个是什么？",
    }
    assert calls[1][1] == {"task_id": "task-1"}
    assert calls[2][1] == {"task_id": "task-1"}

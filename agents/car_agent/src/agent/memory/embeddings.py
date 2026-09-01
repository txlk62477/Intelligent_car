"""Agent Server Store 使用的本机 Ollama 异步 embedding 函数。"""

from __future__ import annotations

import math
import os
from typing import Any

import httpx

EMBEDDING_DIMENSION = 1024


async def aembed_texts(texts: list[str]) -> list[list[float]]:
    """批量调用 Ollama `/api/embed` 并严格校验 1024 维结果。"""
    if not texts:
        return []
    base_url = os.getenv(
        "OLLAMA_EMBEDDING_URL",
        os.getenv("OLLAMA_VISION_URL", "http://host.docker.internal:11434"),
    ).rstrip("/")
    model = os.getenv("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b").strip()
    timeout = float(os.getenv("OLLAMA_EMBEDDING_TIMEOUT", "8"))
    if not base_url or not model or timeout <= 0:
        raise ValueError("Ollama embedding 配置无效")
    async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
        response = await client.post(
            f"{base_url}/api/embed",
            json={"model": model, "input": texts},
        )
        response.raise_for_status()
        payload: Any = response.json()
    vectors = payload.get("embeddings") if isinstance(payload, dict) else None
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise ValueError("Ollama embedding 数量与输入不一致")
    normalized: list[list[float]] = []
    for vector in vectors:
        if not isinstance(vector, list):
            raise ValueError("Ollama embedding 返回格式无效")
        values = [float(value) for value in vector]
        if len(values) != EMBEDDING_DIMENSION:
            raise ValueError(
                f"embedding 维度应为 {EMBEDDING_DIMENSION}，实际为 {len(values)}"
            )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("embedding 包含非有限数值")
        normalized.append(values)
    return normalized

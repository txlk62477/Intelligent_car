"""Intelligent Car Supervisor 的 LangGraph 入口。"""

from __future__ import annotations

import os
from functools import lru_cache

from langchain_deepseek import ChatDeepSeek

from agent.supervisor.graph import SUPERVISOR_PROMPT, build_car_agent_graph


@lru_cache(maxsize=1)
def build_chat_model() -> ChatDeepSeek:
    """从环境变量创建并缓存 DeepSeek 对话模型."""
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，请检查项目根目录下的 .env。")

    return ChatDeepSeek(
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        temperature=0,
        max_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "2048")),
        timeout=60,
        max_retries=1,
    )


graph = build_car_agent_graph(model_factory=build_chat_model)

__all__ = ["SUPERVISOR_PROMPT", "build_chat_model", "graph"]

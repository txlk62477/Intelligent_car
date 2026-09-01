"""长期记忆用户与机器人作用域解析。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """一次运行使用的稳定用户和机器人标识。"""

    user_id: str
    robot_id: str


def resolve_memory_scope(
    config: RunnableConfig,
    runtime: Runtime[Any],
) -> MemoryScope:
    """按认证用户、运行配置和本机默认值解析记忆作用域。"""
    authenticated = _authenticated_identity(runtime)
    configurable = config.get("configurable", {})
    configured_user = configurable.get("user_id")
    user_id = _first_identifier(
        authenticated,
        configured_user,
        os.getenv("CAR_AGENT_USER_ID"),
        "local-user",
    )
    robot_id = _first_identifier(
        configurable.get("robot_id"),
        os.getenv("CAR_ROBOT_ID"),
        "xuegecar-01",
    )
    return MemoryScope(user_id=user_id, robot_id=robot_id)


def _authenticated_identity(runtime: Runtime[Any]) -> str | None:
    server_info = runtime.server_info
    user = None if server_info is None else server_info.user
    if user is None or not bool(getattr(user, "is_authenticated", False)):
        return None
    identity = getattr(user, "identity", None)
    return identity if isinstance(identity, str) else None


def _first_identifier(*candidates: Any) -> str:
    for candidate in candidates:
        if isinstance(candidate, str):
            value = candidate.strip()
            if value:
                if len(value) > 128:
                    raise ValueError("记忆作用域标识不能超过 128 个字符")
                return value
    raise ValueError("无法解析记忆作用域标识")

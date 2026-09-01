"""Agent Server Store 长期记忆节点测试。"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import ExecutionInfo, Runtime
from langgraph.store.memory import InMemoryStore

from agent.memory.identity import resolve_memory_scope
from agent.memory.models import FactUpsert, MemoryExtraction
from agent.memory.nodes import MemoryNodes, merge_profile, sanitize_transcript

pytestmark = pytest.mark.anyio


class StructuredMemoryModel:
    """仅实现记忆提炼所需 interface 的测试模型。"""

    def __init__(self, extraction: MemoryExtraction) -> None:
        """保存预设结构化结果。"""
        self.extraction = extraction

    def with_structured_output(self, _schema: Any, **_kwargs: Any) -> Any:
        """返回自身作为结构化模型。"""
        return self

    async def ainvoke(self, _messages: Any) -> MemoryExtraction:
        """返回预设记忆增量。"""
        return self.extraction


def _runtime(store: InMemoryStore) -> Runtime[Any]:
    return Runtime(
        store=store,
        execution_info=ExecutionInfo(
            checkpoint_id="cp-1",
            checkpoint_ns="",
            task_id="task-1",
            thread_id="thread-1",
            run_id="run-1",
        ),
    )


def test_scope_uses_configurable_and_robot_defaults() -> None:
    scope = resolve_memory_scope(
        {"configurable": {"user_id": "u-1", "robot_id": "car-2"}},
        Runtime(),
    )
    assert scope.user_id == "u-1"
    assert scope.robot_id == "car-2"


def test_merge_profile_replaces_fact_by_stable_key() -> None:
    first = merge_profile(
        {},
        MemoryExtraction(
            episode_summary="用户自我介绍。",
            fact_upserts=[FactUpsert(key="home-city", content="住在北京")],
        ),
        user_id="u-1",
        thread_id="t-1",
        run_id="r-1",
    )
    second = merge_profile(
        first.model_dump(mode="json"),
        MemoryExtraction(
            episode_summary="用户纠正城市。",
            fact_upserts=[FactUpsert(key="home-city", content="住在上海")],
        ),
        user_id="u-1",
        thread_id="t-2",
        run_id="r-2",
    )
    assert len(second.facts) == 1
    assert second.facts[0].content == "住在上海"
    assert second.facts[0].source_run_id == "r-2"


def test_transcript_redacts_secrets_and_base64() -> None:
    transcript = sanitize_transcript(
        [
            HumanMessage(content="api_key=secret-value " + "A" * 300),
            AIMessage(content="已经处理。"),
        ]
    )
    assert "secret-value" not in transcript
    assert "A" * 100 not in transcript
    assert "<redacted>" in transcript
    assert "<base64-redacted>" in transcript


async def test_finalize_writes_profile_and_episode_then_loads_them() -> None:
    store = InMemoryStore()
    extraction = MemoryExtraction(
        episode_summary="用户希望使用厘米描述距离。",
        distance_unit="厘米",
        important_facts=["用户偏好厘米"],
    )
    nodes = MemoryNodes(model_factory=lambda: StructuredMemoryModel(extraction))
    human = HumanMessage(content="以后距离用厘米", id="human-1")
    state = {
        "messages": [human, AIMessage(content="好的。", id="ai-1")],
        "memory_turn_start_message_id": "human-1",
    }
    runtime = _runtime(store)
    result = await nodes.finalize(
        state,
        {"configurable": {"user_id": "u-1", "robot_id": "car-1"}},
        runtime,
    )
    assert result["memory_saved"] is True
    profile = store.get(("users", "u-1", "profile"), "current")
    episode = store.get(("users", "u-1", "episodes"), "run-1")
    assert profile is not None
    assert profile.value["preferences"]["distance_unit"] == "厘米"
    assert episode is not None
    assert episode.value["summary"] == "用户希望使用厘米描述距离。"

    loaded = await nodes.load(
        {"messages": [HumanMessage(content="距离怎么说？", id="human-2")]},
        {"configurable": {"user_id": "u-1", "robot_id": "car-1"}},
        runtime,
    )
    assert loaded["memory_profile"]["preferences"]["distance_unit"] == "厘米"
    assert loaded["memory_episodes"]
    assert "不可信背景资料" in loaded["memory_context"]

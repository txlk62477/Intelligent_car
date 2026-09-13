"""真实模型集成测试：验证消息协议与 Agent 请求接口。"""

import os

import pytest
from langgraph.checkpoint.memory import MemorySaver
from unit_tests.fakes import FakeRobotGateway

from agent import graph
from agent.graph import build_chat_model
from agent.supervisor.graph import build_car_agent_graph

pytestmark = pytest.mark.anyio

_has_real_key = (
    bool(os.getenv("DEEPSEEK_API_KEY", "").strip())
    and os.getenv("DEEPSEEK_API_KEY") != "test-key-for-local-checks"
)

_skip_without_key = pytest.mark.skipif(
    not _has_real_key,
    reason="未配置真实 DEEPSEEK_API_KEY，跳过联网集成测试",
)


@pytest.mark.langsmith
@_skip_without_key
async def test_agent_answers_a_question() -> None:
    inputs = {"messages": [("user", "请只回答：连接正常")]}
    res = await graph.ainvoke(inputs)
    assert res["messages"][-1].content


@pytest.mark.langsmith
@_skip_without_key
async def test_agent_requests_motion_workflow_with_valid_arguments() -> None:
    """真实模型必须按编排协议一次给出合法参数，并停在人工确认。"""
    gateway = FakeRobotGateway(submit_results=[{"status": "SUCCEEDED"}])
    app = build_car_agent_graph(
        model_factory=build_chat_model,
        gateway_factory=lambda: gateway,
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "real-motion-1"}}

    result = await app.ainvoke(
        {"messages": [("user", "请让小车前进 1 米，然后停下")]}, config=config
    )

    interrupts = result.get("__interrupt__") or []
    assert interrupts, "真实模型应通过 request_workflow 提交请求并触发人工确认"
    payload = interrupts[0].value
    assert payload["type"] == "confirm_robot_motion"
    assert payload["actions"] == [{"type": "forward", "mode": "distance", "value": 1.0}]
    # 未确认前不得有任何运动提交。
    assert gateway.submitted == []

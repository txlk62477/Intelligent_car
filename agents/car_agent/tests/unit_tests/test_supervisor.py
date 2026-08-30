"""Supervisor 路由：问答、状态、急停、移动委派与拒绝路径。"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import agent.tools.robot as robot_tools
import agent.tools.vision as vision_tools
from agent.supervisor.graph import build_car_agent_graph
from agent.vision.recognizer import VisionResult
from unit_tests.fakes import FakeChatModel, FakeRobotGateway, tool_call_ai

pytestmark = pytest.mark.anyio


def _build_app(
    model: FakeChatModel,
    gateway: FakeRobotGateway,
):
    return build_car_agent_graph(
        model_factory=lambda: model,
        gateway_factory=lambda: gateway,
        checkpointer=MemorySaver(),
    )


def _tool_messages(result: dict[str, Any]) -> list[ToolMessage]:
    return [
        message for message in result["messages"] if isinstance(message, ToolMessage)
    ]


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _patch_direct_gateway(
    monkeypatch: pytest.MonkeyPatch,
    gateway: FakeRobotGateway,
) -> None:
    """让直接工具节点中的真实工具使用测试 Gateway。"""
    monkeypatch.setattr(robot_tools, "get_robot_gateway", lambda: gateway)


async def test_plain_question_answers_without_tools() -> None:
    model = FakeChatModel([AIMessage(content="你好，我是小车助手。")])
    gateway = FakeRobotGateway()
    app = _build_app(model, gateway)

    result = await app.ainvoke(
        {"messages": [("user", "你好")]}, config=_config("sup-1")
    )
    assert result["messages"][-1].content == "你好，我是小车助手。"
    assert gateway.status_calls == 0
    assert gateway.stop_calls == 0
    assert len(model.calls) == 1


async def test_status_tool_routes_to_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeChatModel(
        [
            tool_call_ai("get_robot_status", {}),
            AIMessage(content="小车在线，位姿正常。"),
        ]
    )
    gateway = FakeRobotGateway()
    event_loop_thread = threading.get_ident()
    gateway_thread_ids: list[int] = []
    original_get_status = gateway.get_status

    def get_status() -> dict[str, Any]:
        gateway_thread_ids.append(threading.get_ident())
        return original_get_status()

    monkeypatch.setattr(gateway, "get_status", get_status)
    _patch_direct_gateway(monkeypatch, gateway)
    app = _build_app(model, gateway)

    result = await app.ainvoke(
        {"messages": [("user", "小车在线吗？")]}, config=_config("sup-2")
    )
    assert gateway.status_calls == 1
    assert gateway_thread_ids != [event_loop_thread]
    tools = _tool_messages(result)
    assert len(tools) == 1
    assert tools[0].name == "get_robot_status"
    assert result["messages"][-1].content == "小车在线，位姿正常。"


async def test_image_tool_routes_through_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = "/home/lk/car/test/fixtures/esp_vga_q20.jpg"

    class FakeRecognizer:
        async def recognize(
            self, path: Any, question: str | None = None
        ) -> VisionResult:
            assert str(path) == image_path
            assert question == "这个是什么？"
            return VisionResult(
                status="success",
                answer="乡村场景。",
                provider="fake",
                model="fake-vision",
                latency_ms=1.0,
            )

    monkeypatch.setattr(vision_tools, "get_vision_recognizer", lambda: FakeRecognizer())
    model = FakeChatModel(
        [
            tool_call_ai(
                "recognize_image",
                {"image_path": image_path, "question": "这个是什么？"},
            ),
            AIMessage(content="这是乡村场景。"),
        ]
    )
    app = _build_app(model, FakeRobotGateway())

    result = await app.ainvoke(
        {"messages": [("user", "请看看这张图片：" + image_path)]},
        config=_config("sup-image-1"),
    )

    tools = _tool_messages(result)
    assert len(tools) == 1
    assert tools[0].name == "recognize_image"
    assert '"answer": "乡村场景。"' in tools[0].content
    assert result["messages"][-1].content == "这是乡村场景。"


async def test_stop_tool_bypasses_workflow_and_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeChatModel(
        [tool_call_ai("stop_robot", {}), AIMessage(content="已立即停车。")]
    )
    gateway = FakeRobotGateway()
    _patch_direct_gateway(monkeypatch, gateway)
    app = _build_app(model, gateway)

    result = await app.ainvoke(
        {"messages": [("user", "快停下！")]}, config=_config("sup-3")
    )
    assert gateway.stop_calls == 1
    assert gateway.submitted == []
    tools = _tool_messages(result)
    assert len(tools) == 1
    assert tools[0].name == "stop_robot"


async def test_motion_delegation_interrupts_then_reports_cancellation() -> None:
    model = FakeChatModel(
        [
            tool_call_ai(
                "delegate_to_motion_workflow",
                {"actions": [{"type": "forward", "mode": "distance", "value": 1}]},
            ),
            AIMessage(content="运动计划已取消，小车没有移动。"),
        ]
    )
    gateway = FakeRobotGateway()
    app = _build_app(model, gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "sup-motion-1"}}

    interrupted = await app.ainvoke(
        {"messages": [("user", "前进 1 米")]}, config=config
    )
    assert interrupted["__interrupt__"], "预期出现人工确认中断"
    result = await app.ainvoke(Command(resume={"confirmed": False}), config=config)

    assert gateway.submitted == []
    assert gateway.stop_calls == 0
    tools = _tool_messages(result)
    assert any(tool.name == "delegate_to_motion_workflow" for tool in tools)
    handed = json.loads(tools[-1].content)
    assert handed["status"] == "cancelled"
    assert result["messages"][-1].content == "运动计划已取消，小车没有移动。"


async def test_out_of_range_plan_is_rejected_without_interrupt() -> None:
    model = FakeChatModel(
        [
            tool_call_ai(
                "delegate_to_motion_workflow",
                {"actions": [{"type": "forward", "mode": "distance", "value": 100}]},
            ),
            AIMessage(content="100 米超出范围，后续交给 Nav2。"),
        ]
    )
    gateway = FakeRobotGateway()
    app = _build_app(model, gateway)

    result = await app.ainvoke(
        {"messages": [("user", "前进 100 米")]}, config=_config("sup-4")
    )
    assert gateway.submitted == []
    tools = _tool_messages(result)
    assert len(tools) == 1
    assert "动作计划无效" in tools[0].content
    assert result["messages"][-1].content == "100 米超出范围，后续交给 Nav2。"


async def test_tool_node_executes_multiple_direct_calls_if_model_violates_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    multi = AIMessage(
        content="",
        tool_calls=[
            {"name": "stop_robot", "args": {}, "id": "call-1", "type": "tool_call"},
            {
                "name": "get_robot_status",
                "args": {},
                "id": "call-2",
                "type": "tool_call",
            },
        ],
    )
    model = FakeChatModel([multi, AIMessage(content="我每轮只调用一个工具。")])
    gateway = FakeRobotGateway()
    _patch_direct_gateway(monkeypatch, gateway)
    app = _build_app(model, gateway)

    result = await app.ainvoke(
        {"messages": [("user", "边停边查状态")]}, config=_config("sup-5")
    )
    assert gateway.stop_calls == 1
    assert gateway.status_calls == 1
    tools = _tool_messages(result)
    assert len(tools) == 2
    assert {tool.name for tool in tools} == {"stop_robot", "get_robot_status"}


async def test_unknown_tool_call_is_rejected() -> None:
    model = FakeChatModel(
        [tool_call_ai("launch_missiles", {}), AIMessage(content="没有这个工具。")]
    )
    gateway = FakeRobotGateway()
    app = _build_app(model, gateway)

    result = await app.ainvoke(
        {"messages": [("user", "发射")]}, config=_config("sup-6")
    )
    tools = _tool_messages(result)
    assert len(tools) == 1
    assert "not a valid tool" in tools[0].content


async def test_graph_uses_direct_tools_and_handoff_nodes() -> None:
    app = _build_app(FakeChatModel(), FakeRobotGateway())

    node_names = set(app.get_graph().nodes)

    assert "direct_tools" in node_names
    assert "prepare_handoff" in node_names
    assert "relative_motion_workflow" in node_names
    assert "follow_workflow" in node_names
    assert "collect_handoff_result" in node_names
    assert "prepare_motion_handoff" not in node_names
    assert "prepare_follow_handoff" not in node_names
    assert "collect_motion_result" not in node_names
    assert "collect_follow_result" not in node_names
    assert "run_tool" not in node_names


def test_supervisor_binds_detection_and_follow_handoff_tools() -> None:
    model = FakeChatModel()
    _build_app(model, FakeRobotGateway())

    names = {tool.name for tool in model.bound_tools}

    assert "recognize_image" in names
    assert "get_perception_detections" in names
    assert "delegate_to_follow_workflow" in names
    assert "start_follow_target" not in names
    assert "get_follow_task_status" not in names
    assert "cancel_follow_task" not in names


async def test_follow_delegation_resolves_candidates_and_reports_selection() -> None:
    gateway = FakeRobotGateway(
        detections_script=[
            {
                "status": "DETECTED",
                "detections": [
                    {"label": "person", "score": 0.95, "position": "左侧"},
                    {"label": "bottle", "score": 0.87, "position": "中央"},
                ],
            }
        ],
        submit_results=[
            {
                "status": "TIMED_OUT",
                "error_code": "TASK_TIMEOUT",
                "target_visible": True,
            }
        ],
    )
    model = FakeChatModel(
        [
            tool_call_ai(
                "delegate_to_follow_workflow",
                {"target_label": "cup", "timeout_seconds": 60},
            ),
            AIMessage(content="已按你的选择跟踪 bottle 至时限结束。"),
        ]
    )
    app = _build_app(model, gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "sup-follow-1"}}

    interrupted = await app.ainvoke({"messages": [("user", "跟随水杯")]}, config=config)
    assert interrupted["__interrupt__"], "目标不存在时应出现候选选择中断"

    result = await app.ainvoke(Command(resume={"answer": "2"}), config=config)

    assert len(gateway.follow_submitted) == 1
    assert gateway.follow_submitted[0]["target_label"] == "bottle"
    tools = _tool_messages(result)
    assert any(tool.name == "delegate_to_follow_workflow" for tool in tools)
    handed = json.loads(tools[-1].content)
    assert handed["status"] == "success"
    assert handed["target_label"] == "bottle"
    assert result["messages"][-1].content == "已按你的选择跟踪 bottle 至时限结束。"

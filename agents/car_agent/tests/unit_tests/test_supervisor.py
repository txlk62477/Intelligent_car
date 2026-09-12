"""薄路由 + 灵活 Agent 架构：问答、状态、急停短路、委派与拒绝路径。"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import agent.tools.robot as robot_tools
import agent.tools.vision as vision_tools
from agent.supervisor import graph as supervisor_graph
from agent.supervisor.graph import build_car_agent_graph
from agent.tools import FLEXIBLE_TOOLS, ROUTER_TOOLS
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
    """让灵活 Agent 工具与急停节点中的真实工具使用测试 Gateway。"""
    monkeypatch.setattr(robot_tools, "get_robot_gateway", lambda: gateway)


async def test_plain_question_routes_to_flexible_agent() -> None:
    model = FakeChatModel(
        [
            AIMessage(content="（路由：无需工具）"),
            AIMessage(content="你好，我是小车助手。"),
        ]
    )
    gateway = FakeRobotGateway()
    app = _build_app(model, gateway)

    result = await app.ainvoke(
        {"messages": [("user", "你好")]}, config=_config("sup-1")
    )
    assert result["messages"][-1].content == "你好，我是小车助手。"
    assert gateway.status_calls == 0
    assert gateway.stop_calls == 0
    # 一次路由调用 + 一次灵活 Agent 调用。
    assert len(model.calls) == 2


async def test_status_tool_routes_to_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeChatModel(
        [
            AIMessage(content="（路由：无需工具）"),
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


async def test_image_tool_routes_through_flexible_agent(
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
            AIMessage(content="（路由：无需工具）"),
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


async def test_stop_short_circuits_without_flexible_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeChatModel([tool_call_ai("stop_robot", {})])
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
    assert result["messages"][-1].content == "已立即发送停车指令。"
    # 急停短路：只有路由一次模型调用，灵活 Agent 未被调用。
    assert len(model.calls) == 1


async def test_motion_delegation_interrupts_then_reports_cancellation() -> None:
    model = FakeChatModel(
        [
            tool_call_ai(
                "delegate_to_motion_workflow",
                {"actions": [{"type": "forward", "mode": "distance", "value": 1}]},
            ),
            AIMessage(content="（路由重入：步骤结束）"),
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
    # 路由两次（首轮委派 + 重入收尾判断）+ 灵活 Agent 一次。
    assert len(model.calls) == 3


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


async def test_multi_tool_call_is_rejected_without_execution(
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
    assert gateway.stop_calls == 0
    assert gateway.status_calls == 0
    tools = _tool_messages(result)
    assert len(tools) == 1
    assert "路由必须一次只调用一个工具" in tools[0].content
    assert result["messages"][-1].content == "我每轮只调用一个工具。"


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
    assert "未知路由工具" in tools[0].content
    assert result["messages"][-1].content == "没有这个工具。"


async def test_router_receives_trimmed_context() -> None:
    """薄路由只应看到最近 ROUTING_MESSAGE_COUNT 条消息。"""
    model = FakeChatModel(
        [
            AIMessage(content="（路由：无需工具）"),
            AIMessage(content="已收到。"),
        ]
    )
    app = _build_app(model, FakeRobotGateway())

    history = [
        message
        for index in range(10)
        for message in (
            ("user", f"历史问题 {index}"),
            ("assistant", f"历史回答 {index}"),
        )
    ]
    history.append(("user", "你好"))
    await app.ainvoke({"messages": history}, config=_config("sup-trim-1"))

    router_input = model.calls[0]
    # 系统提示 + 最近 ROUTING_MESSAGE_COUNT 条。
    assert len(router_input) == 1 + supervisor_graph.ROUTING_MESSAGE_COUNT
    assert router_input[-1].content == "你好"


async def test_summarization_middleware_compacts_flexible_agent_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """长对话应触发官方 SummarizationMiddleware 压缩灵活 Agent 的历史。"""
    monkeypatch.setattr(supervisor_graph, "SUMMARIZE_TRIGGER_TOKENS", 100)
    model = FakeChatModel(
        [
            AIMessage(content="（路由：无需工具）"),
            AIMessage(content="这是滚动摘要。"),
            AIMessage(content="我已根据摘要继续。"),
        ]
    )
    app = _build_app(model, FakeRobotGateway())

    history = [
        message
        for index in range(12)
        for message in (
            ("user", f"历史问题 {index}：" + "内容" * 30),
            ("assistant", f"历史回答 {index}：" + "内容" * 30),
        )
    ]
    history.append(("user", "继续"))
    result = await app.ainvoke({"messages": history}, config=_config("sup-sum-1"))

    assert result["messages"][-1].content == "我已根据摘要继续。"
    # 摘要模型被调用：输入应包含官方摘要提示（官方实现以字符串提示调用摘要模型）。
    summarizer_calls = [
        call
        for call in model.calls
        if "SESSION INTENT" in "".join(str(message) for message in call)
    ]
    assert len(summarizer_calls) == 1
    # 压缩后的历史以官方摘要 HumanMessage 开头。
    compacted = [
        message
        for call in model.calls
        for message in call
        if isinstance(message, HumanMessage)
        and str(message.content).startswith("Here is a summary of the conversation")
    ]
    assert compacted, "灵活 Agent 输入应包含官方摘要占位消息"
    assert "这是滚动摘要。" in str(compacted[-1].content)


async def test_graph_uses_thin_router_and_flexible_agent_nodes() -> None:
    app = _build_app(FakeChatModel(), FakeRobotGateway())

    node_names = set(app.get_graph().nodes)

    assert "thin_router" in node_names
    assert "flexible_agent" in node_names
    assert "stop" in node_names
    assert "prepare_handoff" in node_names
    assert "relative_motion_workflow" in node_names
    assert "follow_workflow" in node_names
    assert "collect_handoff_result" in node_names
    assert "supervisor" not in node_names
    assert "direct_tools" not in node_names
    assert "prepare_motion_handoff" not in node_names
    assert "prepare_follow_handoff" not in node_names
    assert "collect_motion_result" not in node_names
    assert "collect_follow_result" not in node_names


def test_router_and_flexible_tool_sets_are_split() -> None:
    router_names = {tool.name for tool in ROUTER_TOOLS}
    flexible_names = {tool.name for tool in FLEXIBLE_TOOLS}

    assert "stop_robot" in router_names
    assert "delegate_to_motion_workflow" in router_names
    assert "delegate_to_follow_workflow" in router_names
    assert "delegate_to_save_location_workflow" in router_names
    assert "delegate_to_delete_location_workflow" in router_names
    assert "delegate_to_navigation_workflow" in router_names
    assert flexible_names == {"get_robot_status", "recognize_image"}
    assert "get_robot_status" not in router_names
    assert "recognize_image" not in router_names
    assert "get_perception_detections" not in router_names | flexible_names
    assert "start_follow_target" not in router_names | flexible_names
    assert "get_follow_task_status" not in router_names | flexible_names
    assert "cancel_follow_task" not in router_names | flexible_names


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
            AIMessage(content="（路由重入：步骤结束）"),
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


async def test_multi_step_plan_continues_after_first_step_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """路由循环：上一步完成后，路由模型应继续委派下一步（移动 → 急停）。"""
    model = FakeChatModel(
        [
            tool_call_ai(
                "delegate_to_motion_workflow",
                {"actions": [{"type": "forward", "mode": "distance", "value": 1}]},
                call_id="call-m1",
            ),
            tool_call_ai("stop_robot", {}, call_id="call-m2"),
        ]
    )
    gateway = FakeRobotGateway(submit_results=[{"status": "SUCCEEDED"}])
    _patch_direct_gateway(monkeypatch, gateway)
    app = _build_app(model, gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "sup-multi-1"}}

    interrupted = await app.ainvoke(
        {"messages": [("user", "前进 1 米，然后停下")]}, config=config
    )
    assert interrupted["__interrupt__"], "移动前应出现人工确认中断"
    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)

    assert len(gateway.submitted) == 1
    assert gateway.stop_calls == 1
    assert result["messages"][-1].content == "已立即发送停车指令。"
    # 两步各一次路由调用，无灵活 Agent 参与。
    assert len(model.calls) == 2


async def test_router_step_cap_forces_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """达到 ROUTER_MAX_STEPS 后不再咨询路由模型，交给灵活 Agent 收尾。"""
    monkeypatch.setattr(supervisor_graph, "ROUTER_MAX_STEPS", 1)
    model = FakeChatModel(
        [
            tool_call_ai(
                "delegate_to_motion_workflow",
                {"actions": [{"type": "forward", "mode": "distance", "value": 1}]},
            ),
            AIMessage(content="已到步骤上限，任务结束。"),
        ]
    )
    gateway = FakeRobotGateway(submit_results=[{"status": "SUCCEEDED"}])
    app = _build_app(model, gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "sup-cap-1"}}

    interrupted = await app.ainvoke(
        {"messages": [("user", "前进 1 米")]}, config=config
    )
    assert interrupted["__interrupt__"], "移动前应出现人工确认中断"
    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)

    assert len(gateway.submitted) == 1
    assert result["messages"][-1].content == "已到步骤上限，任务结束。"
    # 第一次路由 + 灵活 Agent 收尾；上限触发时不再调用路由模型。
    assert len(model.calls) == 2

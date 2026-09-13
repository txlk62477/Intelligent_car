"""主图协议：Agent 判断、代码边界、编排调度与终态收敛。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import agent.supervisor.graph as supervisor_graph
import agent.tools.robot as robot_tools
import agent.tools.vision as vision_tools
from agent.common.robot_gateway import RobotGatewayError
from agent.supervisor.graph import SupervisorNodes, build_car_agent_graph
from agent.tools import AGENT_TOOLS
from agent.vision.recognizer import VisionResult
from unit_tests.fakes import FakeChatModel, FakeRobotGateway, tool_call_ai

pytestmark = pytest.mark.anyio

_MOTION_ARGS = {"actions": [{"type": "forward", "mode": "distance", "value": 1}]}


def _build_app(
    model: FakeChatModel,
    gateway: FakeRobotGateway,
    *,
    checkpointer: MemorySaver | None = None,
):
    return build_car_agent_graph(
        model_factory=lambda: model,
        gateway_factory=lambda: gateway,
        checkpointer=checkpointer or MemorySaver(),
    )


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _tools(result: dict[str, Any]) -> list[ToolMessage]:
    return [m for m in result["messages"] if isinstance(m, ToolMessage)]


def _tool_payload(message: ToolMessage) -> dict[str, Any]:
    return json.loads(message.content)


async def _values(app: Any, config: dict[str, Any]) -> dict[str, Any]:
    return dict((await app.aget_state(config)).values)


def _request(
    kind: str,
    arguments: Any,
    call_id: str = "call-1",
    *,
    remaining: list[str] | None = None,
    description: str = "执行当前步骤",
) -> AIMessage:
    return tool_call_ai(
        "request_workflow",
        {
            "kind": kind,
            "arguments": arguments,
            "step_description": description,
            "remaining_goals_after_success": [] if remaining is None else remaining,
        },
        call_id=call_id,
    )


def _patch_gateway_tools(
    monkeypatch: pytest.MonkeyPatch, gateway: FakeRobotGateway
) -> None:
    """只读工具走测试 Gateway（控制类调用由主图截获，不会真正执行工具）。"""
    monkeypatch.setattr(robot_tools, "get_robot_gateway", lambda: gateway)


@pytest.fixture(autouse=True)
def _fast_workflow_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Workflow 轮询间隔降到毫秒级，测试不等待真实时间。"""
    for name in (
        "MOTION_POLL_INTERVAL",
        "FOLLOW_POLL_INTERVAL",
        "NAVIGATION_POLL_INTERVAL",
    ):
        monkeypatch.setenv(name, "0.005")
    monkeypatch.setenv("FOLLOW_TIMEOUT_GRACE", "0.05")


@pytest.fixture(autouse=True)
def _fixed_uuids(monkeypatch: pytest.MonkeyPatch) -> None:
    """固定 uuid4，使 task_id、observation_id 与 operation_id 可预期。"""

    class _FixedUuid:
        hex = "fixed"

        def __str__(self) -> str:
            return "fixed"

    monkeypatch.setattr(supervisor_graph, "uuid4", lambda: _FixedUuid())


# --------------------------------------------------------------------------- #
# 只读路径
# --------------------------------------------------------------------------- #


async def test_plain_question_answers_without_touching_gateway() -> None:
    model = FakeChatModel([AIMessage(content="你好，我是小车助手。")])
    gateway = FakeRobotGateway()
    app = _build_app(model, gateway)

    result = await app.ainvoke({"messages": [("user", "你好")]}, config=_config("q-1"))
    state = await _values(app, _config("q-1"))

    assert result["messages"][-1].content == "你好，我是小车助手。"
    assert gateway.status_calls == 0 and gateway.stop_calls == 0
    assert state["task_status"] == "completed"
    assert state["task_id"] == "task-fixed"
    assert state["dispatch_count"] == 0
    assert _tools(result) == []


async def test_read_only_status_becomes_referencable_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeChatModel(
        [
            tool_call_ai("get_robot_status", {}),
            AIMessage(content="小车在线。"),
        ]
    )
    gateway = FakeRobotGateway()
    _patch_gateway_tools(monkeypatch, gateway)
    app = _build_app(model, gateway)
    config = _config("obs-1")

    result = await app.ainvoke({"messages": [("user", "小车在线吗？")]}, config=config)
    state = await _values(app, config)

    assert gateway.status_calls == 1
    assert result["messages"][-1].content == "小车在线。"
    tool = _tools(result)[0]
    assert tool.name == "get_robot_status"
    assert _tool_payload(tool)["observation_id"] == "obs-fixed"
    assert state["observation_count"] == 1
    assert state["observations"] == [
        {"observation_id": "obs-fixed", "tool_name": "get_robot_status"}
    ]


async def test_image_observation_uses_read_only_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = "/home/lk/car/test/fixtures/esp_vga_q20.jpg"

    class FakeRecognizer:
        async def recognize(
            self, path: Any, question: str | None = None
        ) -> VisionResult:
            assert str(path) == image_path
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
                "recognize_image", {"image_path": image_path, "question": "这是什么？"}
            ),
            AIMessage(content="这是乡村场景。"),
        ]
    )
    app = _build_app(model, FakeRobotGateway())
    config = _config("obs-image")

    result = await app.ainvoke({"messages": [("user", "看看这张图")]}, config=config)
    state = await _values(app, config)

    tool = _tools(result)[0]
    assert tool.name == "recognize_image"
    assert _tool_payload(tool)["observation_id"] == "obs-fixed"
    assert state["observation_count"] == 1
    assert result["messages"][-1].content == "这是乡村场景。"


async def test_observation_budget_stops_further_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supervisor_graph, "OBSERVATION_LIMIT", 1)
    model = FakeChatModel(
        [
            tool_call_ai("get_robot_status", {}, call_id="call-1"),
            tool_call_ai("get_robot_status", {}, call_id="call-2"),
            AIMessage(content="观察预算已用完。"),
        ]
    )
    gateway = FakeRobotGateway()
    _patch_gateway_tools(monkeypatch, gateway)
    app = _build_app(model, gateway)
    config = _config("obs-budget")

    result = await app.ainvoke({"messages": [("user", "连续查两次")]}, config=config)
    state = await _values(app, config)

    assert gateway.status_calls == 1
    assert state["task_status"] == "budget_exhausted"
    assert result["messages"][-1].content == "观察预算已用完。"
    budget_messages = [
        _tool_payload(tool)
        for tool in _tools(result)
        if _tool_payload(tool).get("status") == "budget_exhausted"
    ]
    assert budget_messages


# --------------------------------------------------------------------------- #
# 停车与澄清
# --------------------------------------------------------------------------- #


async def test_stop_robot_cancels_task_without_confirmation() -> None:
    model = FakeChatModel([tool_call_ai("stop_robot", {})])
    gateway = FakeRobotGateway()
    app = _build_app(model, gateway)
    config = _config("stop-1")

    result = await app.ainvoke({"messages": [("user", "快停下！")]}, config=config)
    state = await _values(app, config)

    assert gateway.stop_calls == 1
    assert gateway.submitted == []
    assert state["task_status"] == "cancelled"
    assert result["messages"][-1].content == "已立即发送停车指令，当前任务已取消。"
    # 停车不进入任何 Workflow，也不需要确认中断。
    assert "__interrupt__" not in result
    assert len(model.calls) == 1


async def test_stop_reports_gateway_failure_honestly() -> None:
    model = FakeChatModel([tool_call_ai("stop_robot", {})])

    class BrokenGateway(FakeRobotGateway):
        def stop(self) -> dict[str, Any]:
            raise RobotGatewayError("UNAVAILABLE", "Robot Gateway 不可用")

    app = _build_app(model, BrokenGateway())
    result = await app.ainvoke(
        {"messages": [("user", "停下")]}, config=_config("stop-2")
    )

    assert "停车请求发送失败" in result["messages"][-1].content


async def test_clarification_keeps_task_alive_until_user_answers() -> None:
    model = FakeChatModel(
        [
            tool_call_ai("ask_user", {"question": "要前进多少米？"}),
            AIMessage(content="好的，前进 1 米。"),
        ]
    )
    gateway = FakeRobotGateway()
    app = _build_app(model, gateway)
    config = _config("clarify-1")

    first = await app.ainvoke({"messages": [("user", "往前走走")]}, config=config)
    state_after_first = await _values(app, config)

    assert first["messages"][-1].content == "要前进多少米？"
    assert state_after_first["task_status"] == "awaiting_input"
    assert state_after_first["task_id"] == "task-fixed"

    second = await app.ainvoke({"messages": [("user", "1 米")]}, config=config)
    state_after_second = await _values(app, config)

    assert second["messages"][-1].content == "好的，前进 1 米。"
    # 回答澄清问题延续原任务，不新建 task_id。
    assert state_after_second["task_id"] == "task-fixed"
    assert state_after_second["task_status"] == "completed"


# --------------------------------------------------------------------------- #
# 编排闭环
# --------------------------------------------------------------------------- #


async def test_motion_request_requires_confirmation_then_returns_to_agent() -> None:
    model = FakeChatModel(
        [_request("motion", _MOTION_ARGS), AIMessage(content="已前进 1 米。")]
    )
    gateway = FakeRobotGateway(submit_results=[{"status": "SUCCEEDED"}])
    app = _build_app(model, gateway)
    config = _config("flow-motion")

    interrupted = await app.ainvoke(
        {"messages": [("user", "前进 1 米")]}, config=config
    )
    payload = interrupted["__interrupt__"][0].value
    assert payload["type"] == "confirm_robot_motion"
    state = await _values(app, config)
    assert state["task_status"] == "running"
    assert state["current_step"]["step_id"] == "task-fixed:1"

    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)
    final = await _values(app, config)

    assert gateway.submitted == [
        {
            "operation_id": "task-fixed:1:0",
            "type": "forward",
            "mode": "distance",
            "value": 1.0,
        }
    ]
    assert result["messages"][-1].content == "已前进 1 米。"
    assert final["task_status"] == "completed"
    assert [step["step_id"] for step in final["completed_steps"]] == ["task-fixed:1"]
    assert final["dispatch_count"] == 1
    # 每个 AI 工具调用都有且只有一个配对结果。
    assert [(t.name, t.tool_call_id) for t in _tools(result)] == [
        ("request_workflow", "call-1")
    ]


async def test_invalid_motion_arguments_are_rejected_without_execution() -> None:
    model = FakeChatModel(
        [
            _request(
                "motion",
                {"actions": [{"type": "forward", "mode": "distance", "value": 100}]},
            ),
            AIMessage(content="100 米超出范围，请改用导航。"),
        ]
    )
    gateway = FakeRobotGateway()
    app = _build_app(model, gateway)
    config = _config("flow-invalid")

    result = await app.ainvoke({"messages": [("user", "前进 100 米")]}, config=config)
    state = await _values(app, config)

    assert "__interrupt__" not in result
    assert gateway.submitted == []
    payload = _tool_payload(_tools(result)[0])
    assert payload["status"] == "invalid_request"
    assert "请求参数无效" in payload["error"]
    assert state["task_status"] == "awaiting_input"
    assert result["messages"][-1].content == "100 米超出范围，请改用导航。"


async def test_unknown_workflow_kind_is_rejected() -> None:
    model = FakeChatModel(
        [_request("teleport", {}), AIMessage(content="没有这个能力。")]
    )
    app = _build_app(model, FakeRobotGateway())
    result = await app.ainvoke(
        {"messages": [("user", "瞬移")]}, config=_config("kind-1")
    )

    payload = _tool_payload(_tools(result)[0])
    assert payload["status"] == "invalid_request"
    assert "未知 Workflow 类型" in payload["error"]


async def test_observation_reference_must_belong_to_current_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = tool_call_ai(
        "request_workflow",
        {
            "kind": "motion",
            "arguments": _MOTION_ARGS,
            "source_observation_ids": ["obs-from-another-task"],
        },
    )
    model = FakeChatModel([request, AIMessage(content="观察引用无效。")])
    gateway = FakeRobotGateway()
    _patch_gateway_tools(monkeypatch, gateway)
    app = _build_app(model, gateway)
    result = await app.ainvoke(
        {"messages": [("user", "前进 1 米")]}, config=_config("obs-ref")
    )

    payload = _tool_payload(_tools(result)[0])
    assert payload["status"] == "invalid_request"
    assert "观察引用不属于当前任务" in payload["error"]
    assert gateway.submitted == []


async def test_valid_observation_reference_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = tool_call_ai(
        "request_workflow",
        {
            "kind": "motion",
            "arguments": _MOTION_ARGS,
            "source_observation_ids": ["obs-fixed"],
            "remaining_goals_after_success": [],
        },
    )
    model = FakeChatModel(
        [tool_call_ai("get_robot_status", {}), request, AIMessage(content="已完成。")]
    )
    gateway = FakeRobotGateway(submit_results=[{"status": "SUCCEEDED"}])
    _patch_gateway_tools(monkeypatch, gateway)
    app = _build_app(model, gateway)
    config = _config("obs-ref-ok")

    interrupted = await app.ainvoke(
        {"messages": [("user", "看看再前进 1 米")]}, config=config
    )
    assert interrupted["__interrupt__"][0].value["type"] == "confirm_robot_motion"
    state = await _values(app, config)
    assert state["current_step"]["source_observation_ids"] == ["obs-fixed"]

    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)
    assert result["messages"][-1].content == "已完成。"


async def test_observe_then_follow_runs_through_candidates_and_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验收场景：先观察，目标不在画面时列出候选，选择与执行确认分两步。"""
    image_path = "/home/lk/car/test/fixtures/esp_vga_q20.jpg"

    class FakeRecognizer:
        async def recognize(
            self, path: Any, question: str | None = None
        ) -> VisionResult:
            return VisionResult(
                status="success",
                answer="画面里有一个人和一个瓶子。",
                provider="fake",
                model="fake-vision",
                latency_ms=1.0,
            )

    monkeypatch.setattr(vision_tools, "get_vision_recognizer", lambda: FakeRecognizer())
    model = FakeChatModel(
        [
            tool_call_ai(
                "recognize_image",
                {"image_path": image_path, "question": "有没有杯子？"},
                call_id="call-obs",
            ),
            tool_call_ai(
                "request_workflow",
                {
                    "kind": "follow",
                    "arguments": {"target_label": "cup", "timeout_seconds": 60},
                    "source_observation_ids": ["obs-fixed"],
                    "remaining_goals_after_success": [],
                },
                call_id="call-follow",
            ),
            AIMessage(content="已按你的选择跟随 bottle 到时限结束。"),
        ]
    )
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
    _patch_gateway_tools(monkeypatch, gateway)
    app = _build_app(model, gateway)
    config = _config("observe-follow")

    first = await app.ainvoke(
        {"messages": [("user", "看看有没有杯子，有的话跟随它")]}, config=config
    )
    selection = first["__interrupt__"][0].value
    assert selection["type"] == "select_follow_target"
    assert [item["label"] for item in selection["candidates"]] == ["person", "bottle"]
    assert gateway.follow_submitted == []

    second = await app.ainvoke(Command(resume={"answer": "2"}), config=config)
    confirmation = second["__interrupt__"][0].value
    assert confirmation["type"] == "confirm_follow_target"
    assert confirmation["target_label"] == "bottle"
    assert confirmation["selected_from_list"] is True
    assert gateway.follow_submitted == []

    final = await app.ainvoke(Command(resume={"confirmed": True}), config=config)
    state = await _values(app, config)

    assert gateway.follow_submitted[0]["target_label"] == "bottle"
    assert final["messages"][-1].content == "已按你的选择跟随 bottle 到时限结束。"
    assert state["task_status"] == "completed"
    assert state["current_step"] is None


async def test_multi_step_task_dispatches_each_step_serially() -> None:
    model = FakeChatModel(
        [
            _request(
                "motion", _MOTION_ARGS, call_id="call-1", remaining=["再次前进 1 米"]
            ),
            _request("motion", _MOTION_ARGS, call_id="call-2"),
            AIMessage(content="两步都完成了。"),
        ]
    )
    gateway = FakeRobotGateway(
        submit_results=[{"status": "SUCCEEDED"}, {"status": "SUCCEEDED"}]
    )
    app = _build_app(model, gateway)
    config = _config("multi-step")

    await app.ainvoke({"messages": [("user", "前进两次，每次 1 米")]}, config=config)
    await app.ainvoke(Command(resume={"confirmed": True}), config=config)
    second = await app.ainvoke(Command(resume={"confirmed": True}), config=config)
    final = await _values(app, config)

    assert [item["operation_id"] for item in gateway.submitted] == [
        "task-fixed:1:0",
        "task-fixed:2:0",
    ]
    assert [step["step_id"] for step in final["completed_steps"]] == [
        "task-fixed:1",
        "task-fixed:2",
    ]
    assert final["dispatch_count"] == 2
    assert second["messages"][-1].content == "两步都完成了。"


# --------------------------------------------------------------------------- #
# 代码边界
# --------------------------------------------------------------------------- #


async def test_multiple_tool_calls_are_all_rejected_with_paired_messages() -> None:
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
    model = FakeChatModel([multi, AIMessage(content="本轮所有调用都已拒绝。")])
    gateway = FakeRobotGateway()
    app = _build_app(model, gateway)
    result = await app.ainvoke(
        {"messages": [("user", "边停边查")]}, config=_config("multi-tool")
    )
    state = await _values(app, _config("multi-tool"))

    assert gateway.stop_calls == 0 and gateway.status_calls == 0
    assert state["task_status"] == "failed"
    assert sorted(tool.tool_call_id for tool in _tools(result)) == ["call-1", "call-2"]
    assert all("均未执行" in _tool_payload(tool)["error"] for tool in _tools(result))
    assert result["messages"][-1].content == "本轮所有调用都已拒绝。"


async def test_unknown_tool_call_never_reaches_gateway() -> None:
    model = FakeChatModel(
        [tool_call_ai("launch_missiles", {}), AIMessage(content="没有这个工具。")]
    )
    gateway = FakeRobotGateway()
    app = _build_app(model, gateway)
    result = await app.ainvoke(
        {"messages": [("user", "发射")]}, config=_config("unknown-tool")
    )

    assert gateway.stop_calls == 0 and gateway.submitted == []
    assert len(_tools(result)) == 1
    assert result["messages"][-1].content == "没有这个工具。"


async def test_dispatch_is_blocked_while_waiting_for_user_input() -> None:
    model = FakeChatModel(
        [
            _request(
                "motion",
                {"actions": [{"type": "forward", "mode": "distance", "value": 100}]},
                call_id="call-1",
            ),
            _request("motion", _MOTION_ARGS, call_id="call-2"),
        ]
    )
    gateway = FakeRobotGateway()
    app = _build_app(model, gateway)
    config = _config("awaiting-block")

    result = await app.ainvoke({"messages": [("user", "前进 100 米")]}, config=config)
    state = await _values(app, config)

    # 第二次请求被代码边界拒绝，本轮以确定性澄清结束，不进入 Workflow。
    assert "__interrupt__" not in result
    assert gateway.submitted == []
    assert state["task_status"] == "awaiting_input"
    assert "请补充或修改信息后再试" in result["messages"][-1].content
    assert len(model.calls) == 2


async def test_agent_decision_budget_ends_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supervisor_graph, "AGENT_DECISION_LIMIT", 1)
    model = FakeChatModel(
        [
            tool_call_ai("get_robot_status", {}, call_id="call-1"),
            tool_call_ai("get_robot_status", {}, call_id="call-2"),
            AIMessage(content="决策预算已耗尽，本轮结束。"),
        ]
    )
    gateway = FakeRobotGateway()
    _patch_gateway_tools(monkeypatch, gateway)
    app = _build_app(model, gateway)
    config = _config("decision-budget")

    result = await app.ainvoke({"messages": [("user", "一直查状态")]}, config=config)
    state = await _values(app, config)

    assert gateway.status_calls == 1
    assert state["task_status"] == "budget_exhausted"
    assert result["messages"][-1].content == "决策预算已耗尽，本轮结束。"


async def test_terminal_task_rejects_further_execution_requests() -> None:
    """取消后的任务即使模型继续请求执行，也不会再进入 Workflow。"""
    model = FakeChatModel(
        [
            _request("motion", _MOTION_ARGS, call_id="call-1"),
            _request("motion", _MOTION_ARGS, call_id="call-2"),
        ]
    )
    gateway = FakeRobotGateway()
    app = _build_app(model, gateway)
    config = _config("terminal-block")

    await app.ainvoke({"messages": [("user", "前进 1 米")]}, config=config)
    result = await app.ainvoke(Command(resume={"confirmed": False}), config=config)
    state = await _values(app, config)

    assert state["task_status"] == "cancelled"
    assert gateway.submitted == []
    assert "__interrupt__" not in result
    # 终态解释阶段没有任何工具能力：最后一次模型调用只有系统提示与任务记录。
    assert len(model.calls[-1]) == 2
    assert isinstance(model.calls[-1][0], SystemMessage)
    assert "终态" in str(model.calls[-1][0].content)


async def test_execution_unknown_ends_task_without_blind_retry() -> None:
    model = FakeChatModel(
        [_request("motion", _MOTION_ARGS), AIMessage(content="状态未知。")]
    )
    gateway = FakeRobotGateway(
        submit_errors=[RobotGatewayError("UNAVAILABLE", "连接超时")],
        query_errors=[RobotGatewayError("UNAVAILABLE", "连接超时")],
    )
    app = _build_app(model, gateway)
    config = _config("unknown-exec")

    await app.ainvoke({"messages": [("user", "前进 1 米")]}, config=config)
    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)
    state = await _values(app, config)

    assert gateway.submitted == []  # 没有盲目重提
    assert state["task_status"] == "failed"
    assert "状态未知" in state["stop_reason"]
    assert state["retry_count"] == 0
    assert result["messages"][-1].content == "状态未知。"


async def test_recovered_submission_reuses_same_operation_id() -> None:
    """提交响应丢失但服务端已有记录：按同一 operation_id 查询后继续。"""
    model = FakeChatModel(
        [_request("motion", _MOTION_ARGS), AIMessage(content="已完成。")]
    )
    gateway = FakeRobotGateway(
        submit_errors=[RobotGatewayError("UNAVAILABLE", "响应丢失")],
        preloaded_motions={
            "task-fixed:1:0": {"operation_id": "task-fixed:1:0", "status": "RUNNING"}
        },
        poll_scripts={"task-fixed:1:0": [{"status": "SUCCEEDED"}]},
    )
    app = _build_app(model, gateway)
    config = _config("recovered")

    await app.ainvoke({"messages": [("user", "前进 1 米")]}, config=config)
    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)
    state = await _values(app, config)

    assert gateway.submitted == []
    assert state["task_status"] == "completed"
    assert result["messages"][-1].content == "已完成。"


async def test_not_found_submission_reports_no_execution() -> None:
    model = FakeChatModel(
        [_request("motion", _MOTION_ARGS), AIMessage(content="未执行。")]
    )
    gateway = FakeRobotGateway(
        submit_errors=[RobotGatewayError("UNAVAILABLE", "响应丢失")]
    )
    app = _build_app(model, gateway)
    config = _config("not-found")

    await app.ainvoke({"messages": [("user", "前进 1 米")]}, config=config)
    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)
    state = await _values(app, config)

    assert state["task_status"] == "failed"
    assert "没有该操作记录" in state["stop_reason"]
    assert result["messages"][-1].content == "未执行。"


# --------------------------------------------------------------------------- #
# 节点级边界
# --------------------------------------------------------------------------- #


def _node_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "messages": [HumanMessage(content="前进 1 米")],
        "schema_version": supervisor_graph.SCHEMA_VERSION,
        "task_id": "task-fixed",
        "task_status": "active",
        "completed_steps": [],
        "observations": [],
        "dispatch_count": 0,
        "retry_count": 0,
    }
    state.update(overrides)
    return state


def _nodes() -> SupervisorNodes:
    return SupervisorNodes(model_factory=lambda: None, gateway_factory=FakeRobotGateway)


def test_old_schema_state_is_rejected() -> None:
    command = _nodes().initialize_task(_node_state(schema_version=1))

    assert command.goto == "terminal_explain"
    assert command.update["task_status"] == "failed"
    assert "旧版状态结构" in command.update["stop_reason"]


def test_new_session_initializes_task_record() -> None:
    command = _nodes().initialize_task({"messages": [HumanMessage(content="你好")]})

    assert command.goto == "flexible_agent"
    assert command.update["schema_version"] == supervisor_graph.SCHEMA_VERSION
    assert command.update["goal"] == "你好"
    assert command.update["completed_steps"] == []


def test_dispatch_budget_is_enforced_before_any_workflow_call() -> None:
    command = _nodes().orchestrate_request(
        _node_state(
            dispatch_count=supervisor_graph.WORKFLOW_DISPATCH_LIMIT,
            pending_workflow_request={
                "request_id": "call-1",
                "kind": "motion",
                "arguments": _MOTION_ARGS,
                "source_observation_ids": [],
            },
        )
    )

    assert command.goto == "terminal_explain"
    assert command.update["task_status"] == "budget_exhausted"
    assert "委派预算" in command.update["stop_reason"]


def test_completed_step_is_not_dispatched_twice() -> None:
    command = _nodes().orchestrate_request(
        _node_state(
            dispatch_count=1,
            completed_steps=[
                {
                    "step_id": "task-fixed:1",
                    "request_id": "call-1",
                    "kind": "motion",
                    "arguments": _MOTION_ARGS,
                    "source_observation_ids": [],
                    "status": "success",
                    "result": {"status": "success", "summary": "已完成"},
                }
            ],
            pending_workflow_request={
                "request_id": "call-1",
                "kind": "motion",
                "arguments": _MOTION_ARGS,
                "source_observation_ids": [],
            },
        )
    )

    assert command.goto == "flexible_agent"
    # 重放同一步不消耗委派预算。
    assert "dispatch_count" not in command.update


def test_resume_reuses_step_id_and_stops_after_retry_limit() -> None:
    step = {
        "step_id": "task-fixed:1",
        "request_id": "call-1",
        "kind": "motion",
        "arguments": _MOTION_ARGS,
        "source_observation_ids": [],
        "status": "running",
        "result": None,
    }
    first = _nodes().orchestrate_request(
        _node_state(
            current_step=step,
            task_status="running",
            dispatch_count=1,
            pending_workflow_request={
                "request_id": "call-1",
                "kind": "motion",
                "arguments": _MOTION_ARGS,
                "source_observation_ids": [],
            },
        )
    )
    assert first.goto == "relative_motion_workflow"
    assert first.update["motion_plan_id"] == "task-fixed:1"
    assert first.update["retry_count"] == 1
    assert "dispatch_count" not in first.update

    second = _nodes().orchestrate_request(
        _node_state(
            current_step=step,
            task_status="running",
            dispatch_count=1,
            retry_count=supervisor_graph.STEP_RETRY_LIMIT,
            pending_workflow_request={
                "request_id": "call-1",
                "kind": "motion",
                "arguments": _MOTION_ARGS,
                "source_observation_ids": [],
            },
        )
    )
    assert second.goto == "terminal_explain"
    assert "恢复次数已耗尽" in second.update["stop_reason"]


# --------------------------------------------------------------------------- #
# 上下文与协议
# --------------------------------------------------------------------------- #


def test_tool_pair_trimming_never_splits_calls_from_results() -> None:
    messages = [
        HumanMessage(content="问题 1"),
        tool_call_ai("get_robot_status", {}, call_id="call-1"),
        ToolMessage(content="{}", tool_call_id="call-1", name="get_robot_status"),
        AIMessage(content="回答 1"),
        HumanMessage(content="问题 2"),
        AIMessage(content="回答 2"),
    ]

    trimmed = supervisor_graph._trim_preserving_tool_pairs(messages, 3)

    assert [type(message).__name__ for message in trimmed] == [
        "AIMessage",
        "HumanMessage",
        "AIMessage",
    ]
    # 被裁掉调用的孤立工具结果不会留在窗口里。
    assert not any(isinstance(message, ToolMessage) for message in trimmed)


def test_tool_pair_trimming_backfills_owner_of_kept_results() -> None:
    messages = [
        HumanMessage(content="问题"),
        tool_call_ai("get_robot_status", {}, call_id="call-1"),
        ToolMessage(content="{}", tool_call_id="call-1", name="get_robot_status"),
        AIMessage(content="回答"),
    ]

    trimmed = supervisor_graph._trim_preserving_tool_pairs(messages, 2)

    assert isinstance(trimmed[0], AIMessage)
    assert trimmed[0].tool_calls[0]["id"] == "call-1"
    assert isinstance(trimmed[1], ToolMessage)


async def test_agent_input_never_carries_orphan_tool_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """裁剪已接线：任何一次模型调用都不会出现无配对调用的工具结果。"""
    monkeypatch.setattr(supervisor_graph, "AGENT_HISTORY_LIMIT", 3)
    model = FakeChatModel(
        [
            tool_call_ai("get_robot_status", {}, call_id="call-1"),
            AIMessage(content="小车在线。"),
            AIMessage(content="收到。"),
            AIMessage(content="好的。"),
        ]
    )
    gateway = FakeRobotGateway()
    _patch_gateway_tools(monkeypatch, gateway)
    app = _build_app(model, gateway)
    config = _config("trim-wiring")

    await app.ainvoke({"messages": [("user", "小车在线吗？")]}, config=config)
    await app.ainvoke({"messages": [("user", "再确认一次")]}, config=config)

    for call in model.calls:
        announced: set[str] = set()
        for message in call:
            if isinstance(message, AIMessage) and message.tool_calls:
                announced |= {str(item["id"]) for item in message.tool_calls}
            if isinstance(message, ToolMessage):
                assert str(message.tool_call_id) in announced, "工具结果必须与调用成组"
        # 上限之外允许：Agent 系统提示、可信上下文，以及最多一条为配对回补的
        # AI 调用消息 —— 保持工具调用成组优先于严格计数。
        assert len(call) <= supervisor_graph.AGENT_HISTORY_LIMIT + 3


async def test_trusted_task_context_is_injected_into_agent_input() -> None:
    model = FakeChatModel(
        [
            tool_call_ai("ask_user", {"question": "前进多少米？"}),
            AIMessage(content="收到。"),
        ]
    )
    app = _build_app(model, FakeRobotGateway())
    config = _config("context-1")

    await app.ainvoke({"messages": [("user", "往前走走")]}, config=config)
    await app.ainvoke({"messages": [("user", "1 米")]}, config=config)

    contexts = [
        message
        for call in model.calls
        for message in call
        if isinstance(message, SystemMessage)
        and "当前任务的可信代码状态" in str(message.content)
    ]
    assert contexts, "Agent 输入应包含代码注入的可信任务上下文"
    payload = json.loads(str(contexts[-1].content).split("\n", 1)[1])
    assert payload["task_id"] == "task-fixed"


def test_graph_exposes_orchestrated_nodes_only() -> None:
    app = _build_app(FakeChatModel(), FakeRobotGateway())
    nodes = set(app.get_graph().nodes)

    assert {
        "initialize_task",
        "flexible_agent",
        "orchestrate_request",
        "collect_handoff_result",
        "terminal_explain",
        "relative_motion_workflow",
        "follow_workflow",
        "map_location_workflow",
        "map_navigation_workflow",
    } <= nodes
    assert {"thin_router", "supervisor", "prepare_handoff", "direct_tools"}.isdisjoint(
        nodes
    )


def test_agent_tool_set_is_read_only_plus_control() -> None:
    assert {tool.name for tool in AGENT_TOOLS} == {
        "get_robot_status",
        "recognize_image",
        "request_workflow",
        "ask_user",
        "stop_robot",
    }

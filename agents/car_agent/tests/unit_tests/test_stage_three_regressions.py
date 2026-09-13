"""阶段三：登记、完整消息组、旧状态拒绝和可展示进度回归。"""

from __future__ import annotations

from typing import Any, get_args

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import agent.supervisor.workflow_registry as registry
from agent.state.car_agent import CarAgentState
from agent.supervisor.checkpoints import SCHEMA_VERSION, IncompatibleCheckpointError
from agent.supervisor.graph import _trim_preserving_tool_pairs, build_car_agent_graph
from agent.tools.orchestration import WorkflowKind
from agent.tools.requests import MotionRequest
from unit_tests.fakes import FakeChatModel, FakeRobotGateway, tool_call_ai
from unit_tests.test_supervisor import (
    _MOTION_ARGS,
    _config,
    _node_state,
    _nodes,
    _request,
)

pytestmark = pytest.mark.anyio


def test_registry_covers_tool_types() -> None:
    assert set(get_args(WorkflowKind)) == registry.WORKFLOW_SPECS.keys()


def test_new_workflow_uses_descriptor_without_supervisor_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = registry.WorkflowSpec(
        MotionRequest,
        lambda arguments, step_id: {"test_input": arguments, "test_id": step_id},
        "test_workflow",
        "test_result",
        "测试任务",
    )
    monkeypatch.setattr(
        registry, "WORKFLOW_SPECS", {**registry.WORKFLOW_SPECS, "test": spec}
    )
    nodes = _nodes()
    arguments = nodes._validate_arguments("test", _MOTION_ARGS)
    command = nodes._prepare_workflow(
        "test", arguments, "request-1", "step-1", {"retry_count": 0}
    )
    assert command.goto == "test_workflow"
    assert command.update["test_id"] == "step-1"
    assert command.update["retry_count"] == 0
    result = {"status": "success", "summary": "完成"}
    assert nodes._workflow_result({"test_result": result}, "test") == result
    assert (
        nodes._workflow_result({"navigation_result": result}, "test")["status"]
        == "failed"
    )
    assert (
        nodes._workflow_result({"navigation_result": result}, "unknown")["status"]
        == "failed"
    )


@pytest.mark.parametrize("limit", [2, 3, 4, 5, 0])
def test_multi_tool_group_keeps_all_results(limit: int) -> None:
    call = AIMessage(
        content="",
        tool_calls=[
            {"name": "a", "args": {}, "id": "a"},
            {"name": "b", "args": {}, "id": "b"},
        ],
    )
    messages = [
        HumanMessage(content="问题"),
        call,
        ToolMessage(content="a", tool_call_id="a"),
        ToolMessage(content="b", tool_call_id="b"),
        AIMessage(content="回答"),
    ]
    kept = _trim_preserving_tool_pairs(messages, limit)
    assert [item.tool_call_id for item in kept if isinstance(item, ToolMessage)] == [
        "a",
        "b",
    ]
    assert kept[-4:] == messages[-4:]


def test_incomplete_or_duplicate_tool_groups_are_dropped() -> None:
    call = AIMessage(
        content="",
        tool_calls=[
            {"name": "a", "args": {}, "id": "a"},
            {"name": "b", "args": {}, "id": "b"},
        ],
    )
    final = AIMessage(content="回答")
    for results in (
        [ToolMessage(content="b", tool_call_id="b")],
        [
            ToolMessage(content="a", tool_call_id="a"),
            ToolMessage(content="a", tool_call_id="a"),
        ],
    ):
        assert _trim_preserving_tool_pairs([call, *results, final], 20) == [final]


def test_unversioned_legacy_fields_are_not_new_task() -> None:
    result = _nodes().initialize_task(
        {"messages": [HumanMessage(content="新消息")], "motion_status": "confirming"}
    )
    assert result.goto == "terminal_explain"
    assert result.update["task_status"] == "failed"


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("injected_saver", [False, True])
async def test_actual_unversioned_checkpoint_rejected_before_any_execution(
    resume: bool, injected_saver: bool
) -> None:
    saver = MemorySaver()
    config = _config(f"old-{resume}-{injected_saver}")
    old_builder = StateGraph(CarAgentState)

    def old_confirm(state: Any) -> dict[str, Any]:
        interrupt({"type": "old_confirmation"})
        return {}

    old_builder.add_node("old_confirm", old_confirm)
    old_builder.add_edge(START, "old_confirm")
    old_builder.add_edge("old_confirm", END)
    await old_builder.compile(checkpointer=saver).ainvoke(
        {"messages": [("user", "前进")]}, config
    )
    before = await saver.aget_tuple(config)
    assert (
        before is not None
        and "schema_version" not in before.checkpoint["channel_values"]
    )
    model, gateway = FakeChatModel([AIMessage(content="不应运行")]), FakeRobotGateway()
    app = build_car_agent_graph(
        model_factory=lambda: model,
        gateway_factory=lambda: gateway,
        checkpointer=None if injected_saver else saver,
    )
    run_config = (
        {"configurable": {**config["configurable"], "__pregel_checkpointer": saver}}
        if injected_saver
        else config
    )
    with pytest.raises(IncompatibleCheckpointError, match="请创建新会话"):
        await app.ainvoke(
            Command(resume=True) if resume else {"messages": [("user", "继续")]},
            run_config,
        )
    after = await saver.aget_tuple(config)
    assert after is not None and after.checkpoint["id"] == before.checkpoint["id"]
    assert model.calls == []
    assert gateway.submitted == [] and gateway.status_calls == 0


def test_sync_checkpoint_entry_rejects_old_state() -> None:
    saver = MemorySaver()
    config = _config("old-sync")
    old_builder = StateGraph(CarAgentState)
    old_builder.add_node("old", lambda state: {})
    old_builder.add_edge(START, "old")
    old_builder.add_edge("old", END)
    old_builder.compile(checkpointer=saver).invoke(
        {"messages": [("user", "旧消息")]}, config
    )
    model = FakeChatModel([])
    app = build_car_agent_graph(model_factory=lambda: model, checkpointer=saver)
    with pytest.raises(IncompatibleCheckpointError):
        app.invoke(None, config)
    assert not model.calls


@pytest.mark.parametrize("arguments", [None, 1, "invalid", [], [["actions", []]], True])
async def test_malformed_arguments_clarify_without_crashing(arguments: Any) -> None:
    model = FakeChatModel(
        [_request("motion", arguments), AIMessage(content="请修正请求参数。")]
    )
    gateway = FakeRobotGateway()
    app = build_car_agent_graph(
        model_factory=lambda: model,
        gateway_factory=lambda: gateway,
        checkpointer=MemorySaver(),
    )
    result = await app.ainvoke({"messages": [("user", "移动")]}, _config("malformed"))
    state = (await app.aget_state(_config("malformed"))).values
    assert state["task_status"] == "awaiting_input"
    assert state["dispatch_count"] == 0
    assert result["task_progress"]["status"] == "awaiting_input"
    assert gateway.submitted == [] and gateway.status_calls == 0
    tool = next(item for item in result["messages"] if isinstance(item, ToolMessage))
    assert "invalid_request" in str(tool.content)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_observation_ids", "obs-1"),
        ("source_observation_ids", 0),
        ("remaining_goals_after_success", "导航"),
        ("remaining_goals_after_success", [""]),
        ("step_description", 1),
        ("operation_id", "injected"),
    ],
)
async def test_malformed_request_envelope_is_rejected(field: str, value: Any) -> None:
    args = {"kind": "motion", "arguments": _MOTION_ARGS, field: value}
    model = FakeChatModel(
        [tool_call_ai("request_workflow", args), AIMessage(content="请修正。")]
    )
    gateway = FakeRobotGateway()
    app = build_car_agent_graph(
        model_factory=lambda: model, gateway_factory=lambda: gateway
    )
    result = await app.ainvoke({"messages": [("user", "前进")]})
    assert result["task_progress"]["status"] == "awaiting_input"
    assert gateway.submitted == [] and gateway.status_calls == 0


async def test_progress_shows_goal_completed_current_and_pending_steps() -> None:
    goal = "先前进 1 米，再后退 1 米"
    backward = {"actions": [{"type": "backward", "mode": "distance", "value": 1}]}
    model = FakeChatModel(
        [
            _request(
                "motion",
                _MOTION_ARGS,
                "call-1",
                remaining=["后退 1 米"],
                description="前进 1 米",
            ),
            _request("motion", backward, "call-2", description="后退 1 米"),
            AIMessage(content="两步已完成。"),
        ]
    )
    gateway = FakeRobotGateway(
        submit_results=[{"status": "SUCCEEDED"}, {"status": "SUCCEEDED"}]
    )
    app = build_car_agent_graph(
        model_factory=lambda: model,
        gateway_factory=lambda: gateway,
        checkpointer=MemorySaver(),
    )
    config = _config("progress")
    first = await app.ainvoke({"messages": [("user", goal)]}, config)
    assert first["task_progress"]["goal"] == goal
    assert first["task_progress"]["steps"] == [
        {"title": "前进 1 米", "status": "running"},
        {"title": "后退 1 米", "status": "pending"},
    ]
    assert (await app.aget_state(config)).values["remaining_goals"] == [goal]
    second = await app.ainvoke(Command(resume=True), config)
    assert second["task_progress"]["completed_count"] == 1
    assert second["task_progress"]["steps"] == [
        {"title": "前进 1 米", "status": "completed"},
        {"title": "后退 1 米", "status": "running"},
    ]
    assert (await app.aget_state(config)).values["remaining_goals"] == ["后退 1 米"]
    final = await app.ainvoke(Command(resume=True), config)
    assert final["task_progress"]["completed_count"] == 2
    assert final["task_progress"]["status"] == "completed"
    assert all(
        step["status"] == "completed" for step in final["task_progress"]["steps"]
    )
    assert (await app.aget_state(config)).values["schema_version"] == SCHEMA_VERSION


@pytest.mark.parametrize(
    "outcome", ["cancelled", "failed", "execution_unknown", "budget_exhausted"]
)
def test_unsuccessful_step_does_not_apply_remaining_goal_proposal(outcome: str) -> None:
    step = {
        "step_id": "step-1",
        "request_id": "call-1",
        "kind": "motion",
        "description": "前进",
        "arguments": _MOTION_ARGS,
        "source_observation_ids": [],
        "status": "running",
        "remaining_goals_after_success": [],
    }
    command = _nodes().collect_handoff_result(
        _node_state(
            goal="前进",
            remaining_goals=["前进"],
            current_step=step,
            pending_handoff_kind="motion",
            motion_result={"status": outcome, "summary": "未成功"},
        )
    )
    assert "remaining_goals" not in command.update
    assert "completed_steps" not in command.update
    assert command.update["task_progress"]["completed_count"] == 0
    assert command.goto == "terminal_explain"


def test_plain_response_cannot_clear_unfinished_workflow_goals() -> None:
    update = _nodes().complete_agent_response(
        _node_state(dispatch_count=1, remaining_goals=["导航"], goal="先移动再导航")
    )
    assert update["task_status"] == "awaiting_input"
    assert "remaining_goals" not in update
    assert update["task_progress"]["steps"] == [{"title": "导航", "status": "pending"}]

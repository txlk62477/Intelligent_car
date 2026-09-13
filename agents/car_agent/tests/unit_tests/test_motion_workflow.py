"""相对移动子图：人工确认、中止、复合动作、失败与超时。"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import agent.workflows.motion.graph as motion_graph_module
from agent.common.robot_gateway import RobotGatewayError
from agent.workflows.motion.graph import build_motion_workflow
from unit_tests.fakes import FailingRobotGateway, FakeRobotGateway

pytestmark = pytest.mark.anyio

_CONFIRMED_ACTIONS = [
    {"type": "turn_left", "mode": "angle", "value": 60},
    {"type": "forward", "mode": "distance", "value": 1},
]


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """轮询间隔降到毫秒级，测试不等待真实时间。"""
    monkeypatch.setenv("MOTION_POLL_INTERVAL", "0.005")


@pytest.fixture(autouse=True)
def _fixed_motion_plan_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """固定内部计划 ID；调用者不再通过子图输入控制该中间状态。"""
    monkeypatch.setattr(motion_graph_module, "uuid4", lambda: "plan-1")


def _build_app(gateway: FakeRobotGateway):
    return build_motion_workflow(
        gateway_factory=lambda: gateway,
        checkpointer=MemorySaver(),
    )


def _interrupt_payload(state: dict[str, Any]) -> Any:
    """从 langgraph 1.x 返回状态里提取单个中断载荷。"""

    interrupts = state.get("__interrupt__") or []
    assert interrupts, "预期出现人工确认中断，但状态中没有 __interrupt__"
    item = interrupts[0]
    return item.value if hasattr(item, "value") else item


def _inputs(actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"motion_actions": actions}


async def _run_with_confirmation(
    app: Any,
    inputs: dict[str, Any],
    config: dict[str, Any],
    confirmed: bool,
) -> dict[str, Any]:
    """先取确认中断，再按确认结果恢复并返回最终状态。"""

    interrupted = await app.ainvoke(inputs, config=config)
    _interrupt_payload(interrupted)
    return await app.ainvoke(Command(resume={"confirmed": confirmed}), config=config)


async def test_confirm_shows_full_plan_and_executes_serially() -> None:
    gateway = FakeRobotGateway(
        submit_results=[{"status": "SUCCEEDED"}, {"status": "SUCCEEDED"}]
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "motion-1"}}

    interrupted = await app.ainvoke(_inputs(_CONFIRMED_ACTIONS), config=config)
    payload = _interrupt_payload(interrupted)
    assert payload["type"] == "confirm_robot_motion"
    assert payload["actions"] == [
        {"type": "turn_left", "mode": "angle", "value": 60.0},
        {"type": "forward", "mode": "distance", "value": 1.0},
    ]
    assert "左转" in payload["summary"] and "前进" in payload["summary"]
    assert "0.27 m/s" in payload["message"]
    assert "0.53 rad/s" in payload["message"]
    assert "不使用雷达避障" in payload["message"]

    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)
    assert result["motion_result"]["status"] == "success"
    assert result["motion_result"]["completed_actions"] == [
        {
            "operation_id": "plan-1:0",
            "type": "turn_left",
            "mode": "angle",
            "value": 60.0,
            "status": "SUCCEEDED",
        },
        {
            "operation_id": "plan-1:1",
            "type": "forward",
            "mode": "distance",
            "value": 1.0,
            "status": "SUCCEEDED",
        },
    ]
    assert gateway.submitted == [
        {
            "operation_id": "plan-1:0",
            "type": "turn_left",
            "mode": "angle",
            "value": 60.0,
        },
        {
            "operation_id": "plan-1:1",
            "type": "forward",
            "mode": "distance",
            "value": 1.0,
        },
    ]


async def test_unconfirmed_plan_moves_nothing() -> None:
    gateway = FakeRobotGateway()
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "motion-2"}}

    result = await _run_with_confirmation(
        app, _inputs(_CONFIRMED_ACTIONS), config, confirmed=False
    )
    assert result["motion_result"]["status"] == "cancelled"
    assert result["motion_result"]["failed_action"] is None
    assert gateway.submitted == []
    assert gateway.stop_calls == 0


async def test_second_action_failure_skips_remaining_actions() -> None:
    gateway = FakeRobotGateway(
        submit_results=[
            {"status": "SUCCEEDED"},
            {
                "status": "FAILED",
                "error_code": "ACTION_TIMEOUT",
                "error": "动作执行超过 60 秒",
            },
        ]
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "motion-3"}}
    inputs = _inputs(
        _CONFIRMED_ACTIONS + [{"type": "backward", "mode": "distance", "value": 0.5}]
    )

    result = await _run_with_confirmation(app, inputs, config, confirmed=True)
    assert result["motion_result"]["status"] == "failed"
    assert [
        item["operation_id"] for item in result["motion_result"]["completed_actions"]
    ] == ["plan-1:0"]
    assert result["motion_result"]["failed_action"]["operation_id"] == "plan-1:1"
    assert len(gateway.submitted) == 2
    assert gateway.stop_calls == 0


async def test_polling_until_running_turns_succeeded() -> None:
    gateway = FakeRobotGateway(
        poll_scripts={
            "plan-1:0": [{"status": "RUNNING"}, {"status": "SUCCEEDED"}],
        }
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "motion-4"}}
    inputs = _inputs([{"type": "forward", "mode": "time", "value": 1}])

    result = await _run_with_confirmation(app, inputs, config, confirmed=True)
    assert result["motion_result"]["status"] == "success"


async def test_workflow_timeout_stops_gateway_and_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOTION_POLL_INTERVAL", "0.005")
    monkeypatch.setenv("MOTION_ACTION_TIMEOUT", "0.05")
    gateway = FakeRobotGateway()
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "motion-5"}}

    result = await _run_with_confirmation(
        app, _inputs(_CONFIRMED_ACTIONS), config, confirmed=True
    )
    assert result["motion_result"]["status"] == "failed"
    assert result["motion_result"]["failed_action"]["error_code"] == "WORKFLOW_TIMEOUT"
    assert gateway.stop_calls == 1


async def test_gateway_error_during_poll_reports_execution_unknown() -> None:
    """动作已提交但状态无法确认：不得当作“没有移动”，也不得重提。"""
    gateway = FailingRobotGateway()
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "motion-6"}}

    result = await _run_with_confirmation(
        app, _inputs(_CONFIRMED_ACTIONS), config, confirmed=True
    )

    assert result["motion_result"]["status"] == "execution_unknown"
    assert "状态未知" in result["motion_result"]["summary"]
    assert result["motion_result"]["failed_action"]["error_code"] == "UNAVAILABLE"
    # 状态未知前必须先尽力停车，并保持同一个 operation_id。
    assert gateway.stop_calls == 1
    assert [item["operation_id"] for item in gateway.submitted] == ["plan-1:0"]


async def test_lost_submit_response_recovers_existing_operation_id() -> None:
    """提交响应丢失但服务端已有记录：按同一 operation_id 查询并继续。"""
    gateway = FakeRobotGateway(
        submit_errors=[RobotGatewayError("UNAVAILABLE", "响应丢失")],
        preloaded_motions={
            "plan-1:0": {"operation_id": "plan-1:0", "status": "RUNNING"}
        },
        poll_scripts={"plan-1:0": [{"status": "SUCCEEDED"}]},
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "motion-8"}}

    result = await _run_with_confirmation(
        app,
        _inputs([{"type": "forward", "mode": "distance", "value": 1}]),
        config,
        True,
    )

    assert result["motion_result"]["status"] == "success"
    assert gateway.submitted == []  # 没有换新 ID 重新提交
    assert result["motion_result"]["completed_actions"][0]["operation_id"] == "plan-1:0"


async def test_submit_without_record_reports_clear_failure() -> None:
    """提交未确认且 Gateway 查无记录：报告失败，不生成新的 operation_id。"""
    gateway = FakeRobotGateway(
        submit_errors=[RobotGatewayError("UNAVAILABLE", "响应丢失")]
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "motion-9"}}

    result = await _run_with_confirmation(
        app,
        _inputs([{"type": "forward", "mode": "distance", "value": 1}]),
        config,
        True,
    )

    assert result["motion_result"]["status"] == "failed"
    failed = result["motion_result"]["failed_action"]
    assert failed["error_code"] == "SUBMIT_NOT_FOUND"
    assert "没有该操作记录" in failed["error"]
    assert gateway.submitted == []


async def test_same_plan_id_never_submits_the_same_action_twice() -> None:
    """同一个 plan_id 重放时 operation_id 不变，Gateway 幂等识别同一次提交。"""
    gateway = FakeRobotGateway(submit_results=[{"status": "SUCCEEDED"}])
    app = _build_app(gateway)
    inputs = _inputs([{"type": "forward", "mode": "distance", "value": 1}])
    inputs["motion_plan_id"] = "task-1:1"

    first = await _run_with_confirmation(
        app, inputs, {"configurable": {"thread_id": "motion-replay-a"}}, True
    )
    second = await _run_with_confirmation(
        app, inputs, {"configurable": {"thread_id": "motion-replay-b"}}, True
    )

    assert first["motion_result"]["status"] == "success"
    assert second["motion_result"]["status"] == "success"
    assert [item["operation_id"] for item in gateway.submitted] == ["task-1:1:0"]


async def test_cancelled_action_maps_to_cancelled_plan() -> None:
    gateway = FakeRobotGateway(
        submit_results=[{"status": "CANCELLED", "error": "收到停止请求"}]
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "motion-10"}}

    result = await _run_with_confirmation(
        app,
        _inputs([{"type": "forward", "mode": "distance", "value": 1}]),
        config,
        True,
    )

    assert result["motion_result"]["status"] == "cancelled"
    assert result["motion_result"]["failed_action"] is None


async def test_invalid_plan_is_rejected_before_confirmation() -> None:
    gateway = FakeRobotGateway()
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "motion-7"}}

    result = await app.ainvoke(
        _inputs([{"type": "forward", "mode": "distance", "value": 100}]), config=config
    )
    assert result["motion_result"]["status"] == "failed"
    assert "动作计划无效" in result["motion_result"]["summary"]
    assert gateway.submitted == []

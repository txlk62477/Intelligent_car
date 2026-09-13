"""跟随子图：目标命中确认、候选选择、取消、重试与失败路径。"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import agent.workflows.follow.graph as follow_graph_module
from agent.common.robot_gateway import RobotGatewayError
from agent.workflows.follow.graph import build_follow_workflow
from unit_tests.fakes import FailingRobotGateway, FakeRobotGateway

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """轮询间隔降到毫秒级，测试不等待真实时间。"""
    monkeypatch.setenv("FOLLOW_POLL_INTERVAL", "0.005")
    monkeypatch.setenv("FOLLOW_TIMEOUT_GRACE", "0.05")


@pytest.fixture(autouse=True)
def _fixed_follow_plan_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """固定内部计划 ID；调用者不再通过子图输入控制该中间状态。"""
    monkeypatch.setattr(follow_graph_module, "uuid4", lambda: "follow-1")


def _build_app(gateway: FakeRobotGateway):
    return build_follow_workflow(
        gateway_factory=lambda: gateway,
        checkpointer=MemorySaver(),
    )


def _inputs(target: str = "cup", timeout: float = 60.0) -> dict[str, Any]:
    return {"follow_target_label": target, "follow_timeout_seconds": timeout}


def _interrupt_payload(state: dict[str, Any]) -> Any:
    interrupts = state.get("__interrupt__") or []
    assert interrupts, "预期出现中断，但状态中没有 __interrupt__"
    item = interrupts[0]
    return item.value if hasattr(item, "value") else item


def _detected(*labels: str) -> dict[str, Any]:
    return {
        "status": "DETECTED",
        "image_width": 640,
        "image_height": 480,
        "detections": [
            {"label": label, "score": 0.9 - 0.1 * index, "position": "中央"}
            for index, label in enumerate(labels)
        ],
    }


_NATURAL_END = {
    "status": "TIMED_OUT",
    "error_code": "TASK_TIMEOUT",
    "elapsed_seconds": 60.0,
    "target_visible": True,
}


async def test_direct_hit_confirms_then_executes_to_natural_end() -> None:
    gateway = FakeRobotGateway(
        detections_script=[_detected("cup", "bottle")],
        submit_results=[_NATURAL_END],
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-1"}}

    interrupted = await app.ainvoke(_inputs("cup"), config=config)
    payload = _interrupt_payload(interrupted)
    assert payload["type"] == "confirm_follow_target"
    assert payload["target_label"] == "cup"
    assert "60 秒" in payload["message"]

    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)
    assert result["follow_result"]["status"] == "success"
    assert result["follow_result"]["target_label"] == "cup"
    assert result["follow_result"]["final_observation"]["target_visible"] is True
    assert gateway.follow_submitted == [
        {
            "operation_id": "follow-follow-1",
            "target_label": "cup",
            "timeout_seconds": 60.0,
        }
    ]


async def test_direct_hit_unconfirmed_moves_nothing() -> None:
    gateway = FakeRobotGateway(detections_script=[_detected("cup")])
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-2"}}

    interrupted = await app.ainvoke(_inputs("cup"), config=config)
    _interrupt_payload(interrupted)
    result = await app.ainvoke(Command(resume={"confirmed": False}), config=config)

    assert result["follow_result"]["status"] == "cancelled"
    assert gateway.follow_submitted == []


async def test_missing_target_selection_is_separate_from_execution_confirmation() -> (
    None
):
    """候选选择只确定目标；开始执行必须再取得一次明确确认。"""
    gateway = FakeRobotGateway(
        detections_script=[_detected("person", "bottle")],
        submit_results=[_NATURAL_END],
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-3"}}

    interrupted = await app.ainvoke(_inputs("cup"), config=config)
    payload = _interrupt_payload(interrupted)
    assert payload["type"] == "select_follow_target"
    assert payload["empty"] is False
    assert [item["label"] for item in payload["candidates"]] == ["person", "bottle"]
    assert "未检测到" in payload["message"]
    assert gateway.follow_submitted == []

    # 选中候选后只出现执行确认，不会直接开始跟随。
    confirm = await app.ainvoke(Command(resume={"answer": "2"}), config=config)
    confirm_payload = _interrupt_payload(confirm)
    assert confirm_payload["type"] == "confirm_follow_target"
    assert confirm_payload["target_label"] == "bottle"
    assert confirm_payload["selected_from_list"] is True
    assert gateway.follow_submitted == []
    assert gateway.detections_calls == 1  # 选择不重新探测

    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)
    assert result["follow_result"]["status"] == "success"
    assert result["follow_result"]["target_label"] == "bottle"
    assert gateway.follow_submitted[0]["target_label"] == "bottle"


async def test_selection_cancel_at_execution_confirmation_submits_nothing() -> None:
    gateway = FakeRobotGateway(
        detections_script=[_detected("person", "bottle")],
        submit_results=[_NATURAL_END],
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-3b"}}

    await app.ainvoke(_inputs("cup"), config=config)
    await app.ainvoke(Command(resume={"answer": "1"}), config=config)
    result = await app.ainvoke(Command(resume={"confirmed": False}), config=config)

    assert result["follow_result"]["status"] == "cancelled"
    assert gateway.follow_submitted == []


async def test_selection_cancel_stops_workflow() -> None:
    gateway = FakeRobotGateway(detections_script=[_detected("person")])
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-4"}}

    interrupted = await app.ainvoke(_inputs("cup"), config=config)
    _interrupt_payload(interrupted)
    result = await app.ainvoke(Command(resume={"answer": "取消"}), config=config)

    assert result["follow_result"]["status"] == "cancelled"
    assert gateway.follow_submitted == []


async def test_redetect_until_target_appears_then_confirms() -> None:
    gateway = FakeRobotGateway(
        detections_script=[
            {"status": "EMPTY", "detections": []},
            _detected("cup"),
        ],
        submit_results=[_NATURAL_END],
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-5"}}

    interrupted = await app.ainvoke(_inputs("cup"), config=config)
    payload = _interrupt_payload(interrupted)
    assert payload["empty"] is True

    again = await app.ainvoke(Command(resume={"answer": "重新检测"}), config=config)
    confirm = _interrupt_payload(again)
    assert confirm["type"] == "confirm_follow_target"

    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)
    assert result["follow_result"]["status"] == "success"
    assert gateway.detections_calls == 2


async def test_empty_candidates_cancel() -> None:
    gateway = FakeRobotGateway(
        detections_script=[{"status": "EMPTY", "detections": []}]
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-6"}}

    interrupted = await app.ainvoke(_inputs("cup"), config=config)
    payload = _interrupt_payload(interrupted)
    assert payload["candidates"] == []
    result = await app.ainvoke(Command(resume={"answer": "取消"}), config=config)

    assert result["follow_result"]["status"] == "cancelled"


async def test_no_camera_frame_fails_with_specific_error() -> None:
    gateway = FakeRobotGateway(
        detections_script=[{"status": "NO_FRAME", "detections": []}]
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-7"}}

    result = await app.ainvoke(_inputs("cup"), config=config)

    assert result["follow_result"]["status"] == "failed"
    assert "相机无画面" in result["follow_result"]["summary"]
    assert gateway.follow_submitted == []


async def test_detection_timeout_fails_with_specific_error() -> None:
    gateway = FakeRobotGateway(
        detections_script=[{"status": "TIMEOUT", "detections": []}]
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-8"}}

    result = await app.ainvoke(_inputs("cup"), config=config)

    assert result["follow_result"]["status"] == "failed"
    assert "未及时产生检测结果" in result["follow_result"]["summary"]


async def test_invalid_selection_retries_with_hint_then_cancels() -> None:
    gateway = FakeRobotGateway(detections_script=[_detected("person")])
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-9"}}

    interrupted = await app.ainvoke(_inputs("cup"), config=config)
    _interrupt_payload(interrupted)
    again = await app.ainvoke(Command(resume={"answer": "随便打"}), config=config)
    payload = _interrupt_payload(again)
    assert "输入无效" in payload["hint"]

    result = await app.ainvoke(Command(resume={"answer": "取消"}), config=config)
    assert result["follow_result"]["status"] == "cancelled"


async def test_workflow_timeout_cancels_follow_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOLLOW_POLL_INTERVAL", "0.005")
    monkeypatch.setenv("FOLLOW_TIMEOUT_GRACE", "0.05")
    gateway = FakeRobotGateway(detections_script=[_detected("cup")])
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-10"}}

    interrupted = await app.ainvoke(_inputs("cup", 0.05), config=config)
    _interrupt_payload(interrupted)
    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)

    assert result["follow_result"]["status"] == "failed"
    assert result["follow_result"]["final_observation"]["error_code"] == (
        "WORKFLOW_TIMEOUT"
    )
    assert gateway.follow_submitted  # 已提交后再取消
    assert gateway._follow_records["follow-follow-1"]["status"] == "CANCELLED"


async def test_gateway_error_during_execution_reports_execution_unknown() -> None:
    """任务已提交但状态无法确认：报告状态未知并尽力取消，而不是普通失败。"""
    gateway = FailingRobotGateway(detections_script=[_detected("cup")])
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-11"}}

    interrupted = await app.ainvoke(_inputs("cup"), config=config)
    _interrupt_payload(interrupted)
    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)

    assert result["follow_result"]["status"] == "execution_unknown"
    assert "状态未知" in result["follow_result"]["summary"]
    assert len(gateway.follow_submitted) == 1  # 同一个 operation_id，没有重提


async def test_selection_flow_then_execution_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候选选择 + 确认后状态未知，同样收敛为 execution_unknown。"""
    monkeypatch.setenv("FOLLOW_POLL_INTERVAL", "0.005")
    gateway = FailingRobotGateway(detections_script=[_detected("person")])
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-13"}}

    await app.ainvoke(_inputs("cup"), config=config)
    await app.ainvoke(Command(resume={"answer": "1"}), config=config)
    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)

    assert result["follow_result"]["status"] == "execution_unknown"


async def test_lost_submit_response_recovers_existing_follow_task() -> None:
    gateway = FakeRobotGateway(
        detections_script=[_detected("cup")],
        submit_errors=[RobotGatewayError("UNAVAILABLE", "响应丢失")],
        preloaded_follows={
            "follow-follow-1": {"operation_id": "follow-follow-1", "status": "STARTING"}
        },
        poll_scripts={"follow-follow-1": [dict(_NATURAL_END)]},
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-14"}}

    interrupted = await app.ainvoke(_inputs("cup"), config=config)
    _interrupt_payload(interrupted)
    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)

    assert result["follow_result"]["status"] == "success"
    assert gateway.follow_submitted == []  # 没有换新 ID 重新提交


async def test_submit_without_follow_record_reports_clear_failure() -> None:
    gateway = FakeRobotGateway(
        detections_script=[_detected("cup")],
        submit_errors=[RobotGatewayError("UNAVAILABLE", "响应丢失")],
    )
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-15"}}

    interrupted = await app.ainvoke(_inputs("cup"), config=config)
    _interrupt_payload(interrupted)
    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)

    assert result["follow_result"]["status"] == "failed"
    observation = result["follow_result"]["final_observation"]
    assert observation["error_code"] == "SUBMIT_NOT_FOUND"
    assert gateway.follow_submitted == []


async def test_invalid_input_is_rejected_before_probing() -> None:
    gateway = FakeRobotGateway()
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-12"}}

    result = await app.ainvoke(
        {"follow_target_label": "", "follow_timeout_seconds": 60.0}, config=config
    )

    assert result["follow_result"]["status"] == "failed"
    assert "跟随请求无效" in result["follow_result"]["summary"]
    assert gateway.detections_calls == 0

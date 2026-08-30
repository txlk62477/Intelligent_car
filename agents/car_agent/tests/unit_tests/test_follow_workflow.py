"""跟随子图：目标命中确认、候选选择、取消、重试与失败路径。"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import agent.workflows.follow.graph as follow_graph_module
from agent.workflows.follow.graph import build_follow_workflow
from unit_tests.fakes import FailingRobotGateway, FakeRobotGateway

pytestmark = pytest.mark.anyio


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


async def test_missing_target_lists_candidates_and_selection_is_confirmation() -> None:
    gateway = FakeRobotGateway(
        detections_script=[
            _detected("person", "bottle"),
        ],
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

    result = await app.ainvoke(Command(resume={"answer": "2"}), config=config)
    assert result["follow_result"]["status"] == "success"
    assert result["follow_result"]["target_label"] == "bottle"
    assert gateway.follow_submitted[0]["target_label"] == "bottle"
    assert gateway.detections_calls == 1  # 选择即确认，不再探测


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


async def test_gateway_error_during_execution_reports_failure() -> None:
    gateway = FailingRobotGateway(detections_script=[_detected("cup")])
    app = _build_app(gateway)
    config: dict[str, Any] = {"configurable": {"thread_id": "follow-11"}}

    interrupted = await app.ainvoke(_inputs("cup"), config=config)
    _interrupt_payload(interrupted)
    result = await app.ainvoke(Command(resume={"confirmed": True}), config=config)

    assert result["follow_result"]["status"] == "failed"


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

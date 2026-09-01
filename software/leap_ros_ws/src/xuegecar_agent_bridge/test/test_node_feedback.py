"""Gateway feedback/result ordering regression tests."""

from threading import Lock
from types import SimpleNamespace

import pytest

from xuegecar_agent_bridge.node import AgentGatewayNode


def gateway_with_active_record(kind: str, status: str) -> AgentGatewayNode:
    """Build the stateful seam without starting ROS clients or the HTTP server."""
    node = object.__new__(AgentGatewayNode)
    node._lock = Lock()
    node._records = {
        "plan-1:0": {
            "operation_id": "plan-1:0",
            "kind": kind,
            "status": status,
        }
    }
    node._goal_handles = {"plan-1:0": object()}
    node._active_operation_id = "plan-1:0"
    node._active_kind = kind
    node._history_size = 100
    return node


def test_motion_terminal_feedback_stays_non_terminal_until_result() -> None:
    node = gateway_with_active_record("motion", "RUNNING")
    message = SimpleNamespace(
        feedback=SimpleNamespace(status="SUCCEEDED", progress=0.2)
    )

    node._motion_feedback("plan-1:0", message)

    assert node._records["plan-1:0"]["status"] == "RUNNING"
    assert node._records["plan-1:0"]["progress"] == pytest.approx(0.2)
    assert node._active_operation_id == "plan-1:0"

    node._finish_record("plan-1:0", {"status": "SUCCEEDED"})

    assert node._records["plan-1:0"]["status"] == "SUCCEEDED"
    assert node._active_operation_id is None


def test_follow_terminal_feedback_stays_non_terminal_until_result() -> None:
    node = gateway_with_active_record("follow", "TRACKING")
    message = SimpleNamespace(
        feedback=SimpleNamespace(
            status="SUCCEEDED",
            elapsed_seconds=2.0,
            target_visible=True,
            confidence=0.9,
            center_error=0.01,
            area_ratio=1.0,
            linear_x=0.0,
            angular_z=0.0,
        )
    )

    node._follow_feedback("plan-1:0", message)

    record = node._records["plan-1:0"]
    assert record["status"] == "TRACKING"
    assert record["target_visible"] is True
    assert node._active_operation_id == "plan-1:0"

    node._finish_record("plan-1:0", {"status": "SUCCEEDED"})

    assert record["status"] == "SUCCEEDED"
    assert node._active_operation_id is None

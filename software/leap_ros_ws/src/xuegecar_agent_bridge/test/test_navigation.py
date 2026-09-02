import math
import time
from threading import Lock
from types import SimpleNamespace

from xuegecar_agent_bridge.node import AgentGatewayNode

from xuegecar_agent_bridge.navigation import (
    goal_has_clearance,
    occupancy_grid_fingerprint,
    pose_quality,
)


def test_static_amcl_pose_remains_usable_after_two_seconds():
    """静止小车的最后一帧合格 AMCL 位姿不应因时间经过而失效。"""
    node = object.__new__(AgentGatewayNode)
    node._lock = Lock()
    node._map = {"map_id": "sha256:test-map"}
    node._amcl_pose = {
        "x": 1.0,
        "y": 2.0,
        "yaw": 0.0,
        "covariance": tuple([0.0] * 36),
    }
    node._amcl_pose_at = time.monotonic() - 2.1
    node._navigation_client = SimpleNamespace(server_is_ready=lambda: True)
    params = {
        "amcl_max_position_std": 0.25,
        "amcl_max_yaw_std": math.radians(20.0),
        "map_name": "room_map",
    }
    node.get_parameter = lambda name: SimpleNamespace(value=params[name])

    result = node._read_navigation_status()

    assert result["status"] == "READY"
    assert result["error"] is None
    assert result["pose_age_seconds"] > 2.0


def test_map_fingerprint_changes_with_map_content():
    common = dict(
        width=2,
        height=2,
        resolution=0.05,
        origin_x=-1.0,
        origin_y=-2.0,
        origin_yaw=0.0,
    )
    first = occupancy_grid_fingerprint(**common, data=[0, 0, -1, 100])
    assert first == occupancy_grid_fingerprint(**common, data=[0, 0, -1, 100])
    assert first != occupancy_grid_fingerprint(**common, data=[0, 1, -1, 100])


def test_pose_quality_uses_xy_and_yaw_covariance():
    covariance = [0.0] * 36
    covariance[0] = 0.04
    covariance[7] = 0.01
    covariance[35] = math.radians(10) ** 2
    ready, position_std, yaw_std = pose_quality(
        covariance, max_position_std=0.25, max_yaw_std=math.radians(20)
    )
    assert ready
    assert position_std == 0.2
    assert math.isclose(yaw_std, math.radians(10))


def test_goal_clearance_rejects_obstacle_and_unknown_cells():
    common = dict(
        width=5,
        height=5,
        resolution=1.0,
        origin_x=0.0,
        origin_y=0.0,
        origin_yaw=0.0,
        clearance=0.0,
    )
    free = [0] * 25
    assert goal_has_clearance(**common, goal_x=2.1, goal_y=2.1, data=free) == (
        True,
        "",
    )
    occupied = free.copy()
    occupied[12] = 100
    assert not goal_has_clearance(
        **common, goal_x=2.1, goal_y=2.1, data=occupied
    )[0]
    unknown = free.copy()
    unknown[12] = -1
    assert not goal_has_clearance(
        **common, goal_x=2.1, goal_y=2.1, data=unknown
    )[0]

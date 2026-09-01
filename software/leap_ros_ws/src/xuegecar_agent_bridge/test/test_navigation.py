import math

from xuegecar_agent_bridge.navigation import (
    goal_has_clearance,
    occupancy_grid_fingerprint,
    pose_quality,
)


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

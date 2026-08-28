import math

import pytest

from xuegecar_agent_bridge.controller import (
    ControllerConfig,
    MotionCommand,
    MotionController,
    MotionMode,
    MotionRejected,
    MotionType,
    OdomSnapshot,
    VelocityCommand,
)


def odom(now, *, x=0.0, y=0.0, yaw=0.0):
    return OdomSnapshot(x, y, yaw, 0.0, 0.0, now)


def command(operation_id="op-1", *, type="forward", mode="distance", value=1.0):
    return MotionCommand(
        operation_id=operation_id,
        type=MotionType(type),
        mode=MotionMode(mode),
        value=value,
    )


def test_default_motion_speeds_match_gateway_configuration():
    config = ControllerConfig()

    assert config.linear_speed == pytest.approx(0.27)
    assert config.angular_speed == pytest.approx(0.53)


def test_distance_motion_closes_loop_and_stops():
    controller = MotionController()
    controller.update_odom(odom(0.0))
    controller.submit(command(), 0.0)

    velocity = controller.tick(0.1)
    assert velocity.linear_x == pytest.approx(0.27)

    controller.update_odom(odom(1.0, x=0.98))
    velocity = controller.tick(1.0)
    assert velocity.linear_x == 0.0
    assert controller.operation("op-1")["status"] == "SUCCEEDED"


def test_turn_accumulates_yaw_across_pi_wrap():
    controller = MotionController()
    controller.update_odom(odom(0.0, yaw=math.radians(179)))
    controller.submit(command(type="turn_left", mode="angle", value=5.0), 0.0)
    controller.update_odom(odom(0.1, yaw=math.radians(-176)))

    velocity = controller.tick(0.1)
    assert velocity.angular_z == 0.0
    assert controller.operation("op-1")["status"] == "SUCCEEDED"


def test_time_motion_uses_elapsed_time():
    controller = MotionController()
    controller.update_odom(odom(0.0))
    controller.submit(command(mode="time", value=1.0), 0.0)

    assert controller.tick(0.9).linear_x > 0.0
    controller.update_odom(odom(1.0, x=0.1))
    assert controller.tick(1.0).linear_x == 0.0
    assert controller.operation("op-1")["status"] == "SUCCEEDED"


def test_stale_odom_stops_active_motion():
    controller = MotionController()
    controller.update_odom(odom(0.0))
    controller.submit(command(), 0.0)

    velocity = controller.tick(1.01)
    assert velocity.linear_x == 0.0
    assert controller.operation("op-1")["status"] == "ODOM_TIMEOUT"


def test_hard_timeout_stops_motion():
    controller = MotionController(ControllerConfig(odom_timeout=100.0))
    controller.update_odom(odom(0.0))
    controller.submit(command(), 0.0)

    velocity = controller.tick(60.0)
    assert velocity.linear_x == 0.0
    assert controller.operation("op-1")["status"] == "TIMED_OUT"


def test_submit_is_idempotent_and_rejects_concurrency():
    controller = MotionController()
    controller.update_odom(odom(0.0))
    first = controller.submit(command(), 0.0)
    repeated = controller.submit(command(), 0.1)
    assert repeated == first

    with pytest.raises(MotionRejected, match="正在执行") as caught:
        controller.submit(command("op-2"), 0.1)
    assert caught.value.code == "BUSY"


def test_stop_cancels_active_motion():
    controller = MotionController()
    controller.update_odom(odom(0.0))
    controller.submit(command(), 0.0)

    status = controller.stop(0.2)
    assert status["gateway_status"] == "IDLE"
    assert controller.operation("op-1")["status"] == "CANCELLED"
    assert controller.tick(0.2).linear_x == 0.0


@pytest.mark.parametrize(
    ("type", "mode", "value"),
    [
        ("forward", "distance", 3.01),
        ("backward", "distance", 0.01),
        ("turn_left", "angle", 181.0),
        ("turn_right", "time", 10.1),
    ],
)
def test_out_of_range_values_are_rejected(type, mode, value):
    controller = MotionController()
    controller.update_odom(odom(0.0))
    with pytest.raises(MotionRejected) as caught:
        controller.submit(command(type=type, mode=mode, value=value), 0.0)
    assert caught.value.code == "OUT_OF_RANGE"


def test_backward_distance_commands_negative_speed():
    controller = MotionController()
    controller.update_odom(odom(0.0))
    controller.submit(command(type="backward", mode="distance", value=1.0), 0.0)

    velocity = controller.tick(0.1)
    assert velocity.linear_x == pytest.approx(-0.27)


def test_approaching_target_slows_down():
    controller = MotionController()
    controller.update_odom(odom(0.0))
    controller.submit(command(value=1.0), 0.0)

    controller.update_odom(odom(0.1, x=0.9))
    velocity = controller.tick(0.1)
    assert 0.0 < velocity.linear_x < 0.27


def test_stop_sequence_publishes_zero_velocity_ticks():
    controller = MotionController()
    controller.update_odom(odom(0.0))
    controller.submit(command(value=0.05), 0.0)
    controller.update_odom(odom(0.1, x=0.05))
    controller.tick(0.1)

    for _ in range(controller.config.stop_publish_count):
        assert controller.tick(0.1) == VelocityCommand(0.0, 0.0)
    assert controller.tick(0.1) is None


@pytest.mark.parametrize(
    ("type", "mode"),
    [
        ("forward", "angle"),
        ("backward", "angle"),
        ("turn_left", "distance"),
        ("turn_right", "distance"),
    ],
)
def test_invalid_mode_combinations_are_rejected(type, mode):
    controller = MotionController()
    controller.update_odom(odom(0.0))
    with pytest.raises(MotionRejected) as caught:
        controller.submit(command(type=type, mode=mode, value=1.0), 0.0)
    assert caught.value.code == "INVALID_MODE"


def test_conflicting_operation_id_is_rejected():
    controller = MotionController()
    controller.update_odom(odom(0.0))
    controller.submit(command(), 0.0)
    controller.stop(0.1)

    with pytest.raises(MotionRejected) as caught:
        controller.submit(command(value=2.0), 0.2)
    assert caught.value.code == "ID_CONFLICT"


def test_submit_without_fresh_odom_is_rejected():
    controller = MotionController()
    controller.update_odom(odom(0.0))
    with pytest.raises(MotionRejected) as caught:
        controller.submit(command(), 1.01)
    assert caught.value.code == "ODOM_TIMEOUT"

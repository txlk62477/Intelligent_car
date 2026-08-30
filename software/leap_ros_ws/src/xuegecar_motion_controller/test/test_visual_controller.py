import pytest
from xuegecar_motion_controller.visual_controller import (
    DetectionBox,
    FollowConfig,
    VisualFollowController,
)


def box(*, cx=320.0, width=100.0, height=100.0, score=0.9):
    return DetectionBox(
        "cup",
        score,
        cx - width / 2,
        240 - height / 2,
        cx + width / 2,
        240 + height / 2,
    )


def controller(*, timeout=60.0):
    value = VisualFollowController("cup", timeout)
    value.start(0.0)
    return value


def establish_reference(value, *, area_width=100.0):
    for index in range(3):
        value.update([box(width=area_width, height=area_width)], 640, 480, index * 0.1)


def test_three_frames_establish_reference_and_align():
    value = controller()
    establish_reference(value)

    snapshot = value.tick(0.3)

    assert snapshot.status == "ALIGNED"
    assert snapshot.area_ratio == pytest.approx(1.0)
    assert snapshot.linear_x == 0.0
    assert snapshot.angular_z == 0.0


def test_turns_before_adjusting_distance():
    value = controller()
    establish_reference(value)
    value.update([box(cx=500.0, width=50.0, height=50.0)], 640, 480, 0.4)

    snapshot = value.tick(0.4)

    assert snapshot.status == "TRACKING"
    assert snapshot.angular_z < 0.0
    assert snapshot.linear_x == 0.0


def test_area_ratio_controls_forward_and_backward_with_deadzone():
    value = controller()
    establish_reference(value)

    value.update([box(width=80.0, height=80.0)], 640, 480, 0.4)
    assert value.tick(0.4).linear_x > 0.0

    value.update([box(width=120.0, height=120.0)], 640, 480, 0.5)
    assert value.tick(0.5).linear_x < 0.0

    value.update([box(width=105.0, height=105.0)], 640, 480, 0.6)
    assert value.tick(0.6).status == "ALIGNED"


def test_missing_target_stops_then_fails_after_ten_seconds():
    value = controller()
    establish_reference(value)
    value.update([], 640, 480, 1.0)

    searching = value.tick(1.0)
    failed = value.tick(11.0)

    assert searching.status == "SEARCHING"
    assert searching.linear_x == 0.0
    assert searching.angular_z == 0.0
    assert failed.status == "FAILED"
    assert failed.error_code == "TARGET_LOST"


def test_no_detection_stream_fails_startup_after_thirty_seconds():
    value = controller()

    snapshot = value.tick(30.0)

    assert snapshot.status == "FAILED"
    assert snapshot.error_code == "STARTUP_TIMEOUT"


def test_total_timeout_is_terminal_and_stops():
    value = controller(timeout=60.0)
    establish_reference(value)

    snapshot = value.tick(60.0)

    assert snapshot.status == "TIMED_OUT"
    assert snapshot.linear_x == 0.0
    assert snapshot.angular_z == 0.0


def test_initial_selection_uses_highest_confidence_then_keeps_same_target():
    value = controller()
    low = box(cx=150.0, score=0.6)
    high = box(cx=500.0, score=0.95)
    value.update([low, high], 640, 480, 0.0)
    moved_high = box(cx=490.0, score=0.55)
    new_high = box(cx=140.0, score=0.99)
    value.update([new_high, moved_high], 640, 480, 0.1)

    snapshot = value.tick(0.1)

    assert snapshot.center_error > 0.0


def test_invalid_image_dimensions_fail_safely():
    value = controller()
    value.update([box()], 0, 480, 0.0)

    snapshot = value.tick(0.0)

    assert snapshot.status == "FAILED"
    assert snapshot.error_code == "INVALID_DETECTIONS"


def test_custom_config_is_used():
    value = VisualFollowController(
        "cup",
        5.0,
        FollowConfig(stable_frames=1, center_deadzone=0.2),
    )
    value.start(0.0)
    value.update([box(cx=350.0)], 640, 480, 0.0)

    assert value.tick(0.0).status == "ALIGNED"

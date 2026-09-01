import pytest
from xuegecar_motion_controller.visual_controller import (
    DetectionBox,
    FollowConfig,
    VisualFollowController,
)


def box(*, label="cup", cx=320.0, width=100.0, height=100.0, score=0.9):
    return DetectionBox(
        label,
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


def test_candidate_does_not_move_until_three_spatially_consistent_frames():
    value = controller()

    value.update([box(cx=500.0)], 640, 480, 0.0)
    first = value.tick(0.0)
    value.update([box(cx=495.0)], 640, 480, 0.1)
    second = value.tick(0.1)
    value.update([box(cx=490.0)], 640, 480, 0.2)
    confirmed = value.tick(0.2)

    assert first.target_visible is False
    assert first.linear_x == 0.0
    assert first.angular_z == 0.0
    assert second.target_visible is False
    assert second.linear_x == 0.0
    assert second.angular_z == 0.0
    assert confirmed.target_visible is True
    assert confirmed.angular_z < 0.0


def test_cup_acquisition_accepts_bottle_and_vase_predictions():
    value = controller()

    value.update([box(label="bottle", cx=330.0)], 640, 480, 0.0)
    value.update([box(label="vase", cx=332.0)], 640, 480, 0.1)
    value.update([box(label="cup", cx=334.0)], 640, 480, 0.2)

    snapshot = value.tick(0.2)
    assert snapshot.target_visible is True
    assert snapshot.status == "ALIGNED"


def test_locked_target_stops_on_nonalias_and_recovers_from_alias():
    value = controller()
    establish_reference(value)

    value.update(
        [box(label="refrigerator", cx=324.0, score=0.40)],
        640,
        480,
        0.3,
    )
    missing = value.tick(0.3)
    value.update(
        [box(label="bottle", cx=324.0, score=0.40)],
        640,
        480,
        0.4,
    )
    recovered = value.tick(0.4)

    assert missing.target_visible is False
    assert missing.linear_x == 0.0
    assert missing.angular_z == 0.0
    assert recovered.target_visible is True
    assert recovered.status == "ALIGNED"


def test_locked_target_prefers_a_container_alias_over_an_overlapping_person():
    value = controller()
    establish_reference(value)

    value.update(
        [
            box(label="person", score=0.90),
            box(label="bottle", cx=324.0, score=0.30),
        ],
        640,
        480,
        0.3,
    )

    snapshot = value.tick(0.3)
    assert snapshot.target_visible is True
    assert snapshot.confidence == pytest.approx(0.30)


def test_locked_target_rejects_a_far_box_even_when_it_has_an_alias_name():
    value = controller()
    establish_reference(value)

    value.update([box(label="bottle", cx=500.0)], 640, 480, 0.3)

    snapshot = value.tick(0.3)
    assert snapshot.target_visible is False
    assert snapshot.status == "SEARCHING"
    assert snapshot.linear_x == 0.0
    assert snapshot.angular_z == 0.0


def test_locked_target_rejects_an_overlapping_box_with_implausible_size():
    value = controller()
    establish_reference(value)

    value.update(
        [box(label="person", width=200.0, height=200.0)],
        640,
        480,
        0.3,
    )

    snapshot = value.tick(0.3)
    assert snapshot.target_visible is False
    assert snapshot.status == "SEARCHING"


def test_spatial_lock_expires_after_point_seven_five_seconds():
    value = controller()
    establish_reference(value)

    value.update([], 640, 480, 0.3)
    value.update([], 640, 480, 1.0)
    value.update([box(label="bottle")], 640, 480, 1.1)
    first = value.tick(1.1)
    value.update([box(label="vase")], 640, 480, 1.2)
    second = value.tick(1.2)
    value.update([box(label="cup")], 640, 480, 1.3)
    confirmed = value.tick(1.3)

    assert first.target_visible is False
    assert second.target_visible is False
    assert confirmed.target_visible is True


def test_spatial_lock_recovers_before_point_seven_five_seconds():
    value = controller()
    establish_reference(value)

    value.update([], 640, 480, 0.3)
    missing = value.tick(0.3)
    value.update(
        [box(label="vase", cx=324.0, score=0.35)],
        640,
        480,
        0.8,
    )
    recovered = value.tick(0.8)

    assert missing.target_visible is False
    assert missing.linear_x == 0.0
    assert missing.angular_z == 0.0
    assert recovered.target_visible is True
    assert recovered.status == "ALIGNED"


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
    value.update([box(cx=380.0, width=80.0, height=80.0)], 640, 480, 0.4)

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
    value.update(
        [box(cx=130.0, score=0.99), box(cx=480.0, score=0.55)],
        640,
        480,
        0.2,
    )

    snapshot = value.tick(0.2)

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

"""Replay a compressed-image rosbag through YOLO and the visual follower.

This is an explicit regression harness rather than a regular pytest test because a
full run depends on a local model and bag and takes about a minute on CPU.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

import cv2
import numpy as np
import rosbag2_py
from rclpy.serialization import deserialize_message
from sensor_msgs.msg import CompressedImage

from xuegecar_motion_controller.visual_controller import (
    DetectionBox,
    VisualFollowController,
)
from yolo_detect.detector import YoloDetector


IMAGE_TOPIC = "/camera/image_raw/compressed"
CONTAINER_LABELS = {"cup", "bottle", "vase"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bag", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", choices=("auto", "gpu", "cpu"), default="cpu")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--end-index", type=int, default=-1)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--trace-start-index", type=int, default=-1)
    return parser.parse_args()


def overlap(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return 0.0 if union <= 0.0 else intersection / union


def select_observed_target(previous, detections):
    if previous is not None and detections:
        matched = max(detections, key=lambda item: overlap(previous.box, item.box))
        if overlap(previous.box, matched.box) >= 0.50:
            return matched, True
    containers = [item for item in detections if item.label in CONTAINER_LABELS]
    if containers:
        return max(containers, key=lambda item: item.score), False
    return None, False


def main() -> int:
    args = parse_args()
    detector = YoloDetector(
        args.model,
        imgsz=640,
        conf_threshold=0.25,
        iou_threshold=0.45,
        device=args.device,
    )
    detector.engine.warmup(640, times=1)
    follower = VisualFollowController("cup", timeout_seconds=3600.0)
    follower.start(0.0)

    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=args.bag, storage_id="mcap"),
        rosbag2_py.ConverterOptions("cdr", "cdr"),
    )

    frame_index = -1
    first_stamp = None
    processed = 0
    previous_target = None
    previous_visible = False
    label_counts: Counter[str] = Counter()
    alias_label_switches_while_locked = 0
    survived_alias_label_switches = 0
    lost_on_alias_label_switches = 0
    rejected_nonalias_switches = 0
    motion_while_invisible = 0
    visible_reacquisitions = 0
    visibility_loss_events = 0
    lost_switch_events = []

    while reader.has_next():
        topic, data, stamp = reader.read_next()
        if topic != IMAGE_TOPIC:
            continue
        frame_index += 1
        if frame_index < args.start_index or frame_index % args.stride:
            continue
        if args.end_index >= 0 and frame_index > args.end_index:
            break
        if first_stamp is None:
            first_stamp = stamp
        now = (stamp - first_stamp) / 1e9
        message = deserialize_message(data, CompressedImage)
        frame = cv2.imdecode(
            np.frombuffer(message.data, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if frame is None:
            continue

        result = detector.detect(frame)
        boxes = [
            DetectionBox(item.label, item.score, *item.box)
            for item in result.detections
        ]
        follower.update(boxes, frame.shape[1], frame.shape[0], now)
        snapshot = follower.tick(now)
        processed += 1
        if args.trace_start_index >= 0 and frame_index >= args.trace_start_index:
            print(
                json.dumps(
                    {
                        "frame": frame_index,
                        "time": round(now, 3),
                        "detections": [
                            {
                                "label": item.label,
                                "score": round(float(item.score), 3),
                                "box": [round(float(value), 1) for value in item.box],
                            }
                            for item in result.detections
                        ],
                        "status": snapshot.status,
                        "visible": snapshot.target_visible,
                    }
                )
            )

        prior_target = previous_target
        observed, geometrically_matched = select_observed_target(
            prior_target, result.detections
        )
        is_geometric_switch = (
            observed is not None
            and prior_target is not None
            and geometrically_matched
            and observed.label != prior_target.label
            and previous_visible
        )
        is_alias_switch = (
            is_geometric_switch
            and prior_target.label in CONTAINER_LABELS
            and observed.label in CONTAINER_LABELS
        )
        if observed is not None:
            label_counts[observed.label] += 1
            previous_target = observed
        if is_alias_switch:
            alias_label_switches_while_locked += 1
            if snapshot.target_visible:
                survived_alias_label_switches += 1
            else:
                lost_on_alias_label_switches += 1
                lost_switch_events.append(
                    {
                        "frame": frame_index,
                        "from": prior_target.label,
                        "to": observed.label,
                        "iou": round(overlap(prior_target.box, observed.box), 4),
                        "score": round(float(observed.score), 4),
                        "status": snapshot.status,
                    }
                )
        elif (
            is_geometric_switch
            and observed.label not in CONTAINER_LABELS
            and not snapshot.target_visible
        ):
            rejected_nonalias_switches += 1

        if snapshot.target_visible and not previous_visible:
            visible_reacquisitions += 1
        elif previous_visible and not snapshot.target_visible:
            visibility_loss_events += 1
        if not snapshot.target_visible and (
            snapshot.linear_x != 0.0 or snapshot.angular_z != 0.0
        ):
            motion_while_invisible += 1
        previous_visible = snapshot.target_visible

    summary = {
        "processed_frames": processed,
        "observed_target_labels": dict(label_counts.most_common()),
        "alias_label_switches_while_locked": alias_label_switches_while_locked,
        "survived_alias_label_switches": survived_alias_label_switches,
        "lost_on_alias_label_switches": lost_on_alias_label_switches,
        "rejected_nonalias_switches": rejected_nonalias_switches,
        "visible_reacquisitions": visible_reacquisitions,
        "visibility_loss_events": visibility_loss_events,
        "motion_while_invisible": motion_while_invisible,
        "lost_switch_events": lost_switch_events,
    }
    print(json.dumps(summary, indent=2))
    return int(
        processed == 0
        or visible_reacquisitions == 0
        or lost_on_alias_label_switches != 0
        or motion_while_invisible != 0
    )


if __name__ == "__main__":
    raise SystemExit(main())

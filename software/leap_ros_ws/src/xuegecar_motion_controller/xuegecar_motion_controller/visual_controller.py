"""不依赖 ROS2 的检测框视觉跟随控制核心。"""

from __future__ import annotations

import math
from dataclasses import dataclass

TERMINAL_FOLLOW_STATUSES = {"CANCELLED", "FAILED", "TIMED_OUT"}


@dataclass(frozen=True)
class FollowConfig:
    """视觉跟随的控制、超时和检测参数。"""

    startup_timeout: float = 30.0
    search_timeout: float = 10.0
    detection_timeout: float = 0.75
    min_confidence: float = 0.5
    stable_frames: int = 3
    center_deadzone: float = 0.10
    area_deadzone: float = 0.15
    max_linear_speed: float = 0.12
    max_angular_speed: float = 0.35
    linear_kp: float = 0.35
    angular_kp: float = 0.70
    min_linear_speed: float = 0.04
    min_angular_speed: float = 0.10


@dataclass(frozen=True)
class DetectionBox:
    """控制核心需要的最小检测框。"""

    label: str
    score: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) * 0.5, (self.y1 + self.y2) * 0.5

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)


@dataclass(frozen=True)
class FollowSnapshot:
    """单次控制输出和对外可观测状态。"""

    status: str
    elapsed_seconds: float
    target_visible: bool
    confidence: float
    center_error: float
    area_ratio: float
    linear_x: float
    angular_z: float
    error_code: str = ""
    error: str = ""


class VisualFollowController:
    """把检测框流转换为低速 Twist 目标。"""

    def __init__(
        self,
        target_label: str,
        timeout_seconds: float,
        config: FollowConfig | None = None,
    ) -> None:
        self.config = config or FollowConfig()
        self.target_label = target_label.strip()
        self.timeout_seconds = timeout_seconds
        if not self.target_label:
            raise ValueError("target_label 不能为空")
        if timeout_seconds <= 0.0:
            raise ValueError("timeout_seconds 必须大于 0")
        self._started_at: float | None = None
        self._last_frame_at: float | None = None
        self._search_started_at: float | None = None
        self._status = "STARTING"
        self._error_code = ""
        self._error = ""
        self._box: DetectionBox | None = None
        self._visible = False
        self._image_width = 0
        self._image_height = 0
        self._reference_samples: list[float] = []
        self._reference_area: float | None = None

    def start(self, now: float) -> None:
        """开始一个新的跟随任务。"""
        self._started_at = now

    def cancel(self, now: float, reason: str = "收到取消请求") -> FollowSnapshot:
        """取消任务并强制输出零速度。"""
        self._finish("CANCELLED", "CANCELLED", reason)
        return self.tick(now)

    def fail(self, now: float, code: str, error: str) -> FollowSnapshot:
        """由外部依赖故障终止任务。"""
        self._finish("FAILED", code, error)
        return self.tick(now)

    def update(
        self,
        boxes: list[DetectionBox],
        image_width: int,
        image_height: int,
        now: float,
    ) -> None:
        """接收一帧检测结果并更新目标匹配。"""
        if self._status in TERMINAL_FOLLOW_STATUSES:
            return
        self._last_frame_at = now
        if image_width <= 0 or image_height <= 0:
            self._finish("FAILED", "INVALID_DETECTIONS", "检测消息缺少有效图像尺寸")
            return
        self._image_width = image_width
        self._image_height = image_height
        candidates = [
            box
            for box in boxes
            if box.label == self.target_label
            and box.score >= self.config.min_confidence
            and box.area > 0.0
        ]
        selected = self._select_candidate(candidates)
        if selected is None:
            self._mark_searching(now)
            if self._reference_area is None:
                self._reference_samples.clear()
            return

        self._box = selected
        self._visible = True
        self._search_started_at = None
        self._status = "TRACKING"
        if self._reference_area is None:
            self._reference_samples.append(selected.area)
            if len(self._reference_samples) >= self.config.stable_frames:
                samples = self._reference_samples[-self.config.stable_frames :]
                self._reference_area = sum(samples) / len(samples)

    def tick(self, now: float) -> FollowSnapshot:
        """推进超时状态并计算当前速度。"""
        if self._started_at is None:
            raise RuntimeError("必须先调用 start()")
        elapsed = max(0.0, now - self._started_at)
        if self._status not in TERMINAL_FOLLOW_STATUSES:
            if elapsed >= self.timeout_seconds:
                self._finish("TIMED_OUT", "TASK_TIMEOUT", "视觉跟随任务达到总时限")
            elif self._last_frame_at is None:
                if elapsed >= self.config.startup_timeout:
                    self._finish("FAILED", "STARTUP_TIMEOUT", "YOLO 启动后未产生检测消息")
            elif now - self._last_frame_at > self.config.detection_timeout:
                self._mark_searching(now)

            if (
                self._status == "SEARCHING"
                and self._search_started_at is not None
                and now - self._search_started_at >= self.config.search_timeout
            ):
                self._finish("FAILED", "TARGET_LOST", "连续 10 秒未识别到目标")

        linear_x = 0.0
        angular_z = 0.0
        center_error = 0.0
        area_ratio = 0.0
        confidence = 0.0 if self._box is None else self._box.score
        if (
            self._status not in TERMINAL_FOLLOW_STATUSES
            and self._visible
            and self._box is not None
        ):
            center_x, _ = self._box.center
            center_error = (center_x - self._image_width * 0.5) / (
                self._image_width * 0.5
            )
            if self._reference_area is not None and self._reference_area > 0.0:
                area_ratio = self._box.area / self._reference_area
            if abs(center_error) > self.config.center_deadzone:
                angular_z = -self._bounded_speed(
                    abs(center_error),
                    self.config.angular_kp,
                    self.config.min_angular_speed,
                    self.config.max_angular_speed,
                ) * math.copysign(1.0, center_error)
                self._status = "TRACKING"
            elif self._reference_area is None:
                self._status = "TRACKING"
            elif area_ratio < 1.0 - self.config.area_deadzone:
                linear_x = self._bounded_speed(
                    1.0 - area_ratio,
                    self.config.linear_kp,
                    self.config.min_linear_speed,
                    self.config.max_linear_speed,
                )
                self._status = "TRACKING"
            elif area_ratio > 1.0 + self.config.area_deadzone:
                linear_x = -self._bounded_speed(
                    area_ratio - 1.0,
                    self.config.linear_kp,
                    self.config.min_linear_speed,
                    self.config.max_linear_speed,
                )
                self._status = "TRACKING"
            else:
                self._status = "ALIGNED"

        return FollowSnapshot(
            status=self._status,
            elapsed_seconds=elapsed,
            target_visible=self._visible,
            confidence=confidence,
            center_error=center_error,
            area_ratio=area_ratio,
            linear_x=linear_x,
            angular_z=angular_z,
            error_code=self._error_code,
            error=self._error,
        )

    def _select_candidate(
        self, candidates: list[DetectionBox]
    ) -> DetectionBox | None:
        if not candidates:
            return None
        if self._box is None:
            return max(candidates, key=lambda box: box.score)
        ranked = sorted(
            candidates,
            key=lambda box: (_iou(self._box, box), -_center_distance(self._box, box)),
            reverse=True,
        )
        selected = ranked[0]
        distance = _center_distance(self._box, selected)
        diagonal = math.hypot(self._image_width, self._image_height)
        if _iou(self._box, selected) < 0.05 and distance > diagonal * 0.25:
            return None
        return selected

    def _mark_searching(self, now: float) -> None:
        self._visible = False
        self._status = "SEARCHING"
        if self._search_started_at is None:
            self._search_started_at = now

    def _finish(self, status: str, code: str, error: str) -> None:
        self._status = status
        self._error_code = code
        self._error = error
        self._visible = False

    @staticmethod
    def _bounded_speed(error: float, kp: float, minimum: float, maximum: float) -> float:
        return min(maximum, max(minimum, error * kp))


def _iou(left: DetectionBox, right: DetectionBox) -> float:
    x1 = max(left.x1, right.x1)
    y1 = max(left.y1, right.y1)
    x2 = min(left.x2, right.x2)
    y2 = min(left.y2, right.y2)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = left.area + right.area - intersection
    return 0.0 if union <= 0.0 else intersection / union


def _center_distance(left: DetectionBox, right: DetectionBox) -> float:
    left_x, left_y = left.center
    right_x, right_y = right.center
    return math.hypot(right_x - left_x, right_y - left_y)

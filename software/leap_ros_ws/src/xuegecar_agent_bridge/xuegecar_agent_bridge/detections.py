"""YOLO 检测结果的缓存、新鲜度判断与序列化。

该模块不依赖 rclpy，Detections 消息由 Gateway 节点解析成字典后交给缓存，
便于在无 ROS2 环境下单测排序、过滤和过期逻辑。
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Any

DETECTED = "DETECTED"
EMPTY = "EMPTY"
NO_FRAME = "NO_FRAME"
TIMEOUT = "TIMEOUT"


def box_position(x1: float, x2: float, image_width: int) -> str:
    """按检测框中心在图像中的横向位置归类为 左侧/中央/右侧。"""
    if image_width <= 0:
        return "未知"
    center = (float(x1) + float(x2)) / 2.0 / float(image_width)
    if center < 1.0 / 3.0:
        return "左侧"
    if center < 2.0 / 3.0:
        return "中央"
    return "右侧"


class DetectionCache:
    """缓存最新一帧检测结果；fresh() 只返回未过期的快照。"""

    def __init__(
        self,
        *,
        max_age: float = 1.5,
        min_score: float = 0.35,
        max_items: int = 8,
    ) -> None:
        """配置新鲜度阈值、置信度下限和单次返回的检测框上限。"""
        self._max_age = max_age
        self._min_score = min_score
        self._max_items = max_items
        self._lock = Lock()
        self._latest: list[dict[str, Any]] = []
        self._latest_at: float | None = None
        self._image_width = 0
        self._image_height = 0

    def store(self, detections: list[dict[str, Any]], width: int, height: int) -> None:
        """缓存一帧检测结果。"""
        with self._lock:
            self._latest = [dict(item) for item in detections]
            self._latest_at = time.monotonic()
            self._image_width = int(width)
            self._image_height = int(height)

    def fresh(self, now: float | None = None) -> dict[str, Any] | None:
        """返回新鲜检测快照；没有数据或已过期时返回 None。"""
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._latest_at is None or now - self._latest_at > self._max_age:
                return None
            items = sorted(
                self._latest, key=lambda item: -float(item.get("score", 0.0))
            )
            items = [
                item for item in items if float(item.get("score", 0.0)) >= self._min_score
            ]
            return {
                "status": DETECTED if items else EMPTY,
                "image_width": self._image_width,
                "image_height": self._image_height,
                "detections": items[: self._max_items],
            }


__all__ = [
    "DETECTED",
    "EMPTY",
    "NO_FRAME",
    "TIMEOUT",
    "DetectionCache",
    "box_position",
]

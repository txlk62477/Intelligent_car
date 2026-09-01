"""地图身份、定位质量与目标栅格校验的纯函数。"""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Sequence


def quaternion_to_yaw(x: float, y: float, z: float, w: float) -> float:
    """把平面导航四元数转换为 yaw。"""
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def yaw_to_quaternion(yaw: float) -> tuple[float, float]:
    """返回平面四元数的 z、w。"""
    return math.sin(yaw / 2.0), math.cos(yaw / 2.0)


def occupancy_grid_fingerprint(
    *,
    width: int,
    height: int,
    resolution: float,
    origin_x: float,
    origin_y: float,
    origin_yaw: float,
    data: Sequence[int],
) -> str:
    """对影响地图坐标语义的 OccupancyGrid 内容生成稳定 SHA-256。"""
    digest = hashlib.sha256()
    digest.update(
        struct.pack(
            "<II4d",
            int(width),
            int(height),
            float(resolution),
            float(origin_x),
            float(origin_y),
            float(origin_yaw),
        )
    )
    digest.update(bytes(int(value) + 1 for value in data))
    return "sha256:" + digest.hexdigest()


def pose_quality(
    covariance: Sequence[float],
    *,
    max_position_std: float,
    max_yaw_std: float,
) -> tuple[bool, float, float]:
    """用 AMCL x/y/yaw 方差判断二维定位质量。"""
    if len(covariance) < 36:
        return False, math.inf, math.inf
    position_std = math.sqrt(max(0.0, float(covariance[0]), float(covariance[7])))
    yaw_std = math.sqrt(max(0.0, float(covariance[35])))
    return (
        position_std <= max_position_std and yaw_std <= max_yaw_std,
        position_std,
        yaw_std,
    )


def goal_has_clearance(
    *,
    goal_x: float,
    goal_y: float,
    width: int,
    height: int,
    resolution: float,
    origin_x: float,
    origin_y: float,
    origin_yaw: float,
    data: Sequence[int],
    clearance: float,
    occupied_threshold: int = 65,
) -> tuple[bool, str]:
    """检查目标及圆形净空范围是否都在地图已知自由栅格内。"""
    if width <= 0 or height <= 0 or resolution <= 0 or len(data) != width * height:
        return False, "地图栅格无效"
    dx, dy = goal_x - origin_x, goal_y - origin_y
    cosine, sine = math.cos(origin_yaw), math.sin(origin_yaw)
    local_x = cosine * dx + sine * dy
    local_y = -sine * dx + cosine * dy
    center_x = math.floor(local_x / resolution)
    center_y = math.floor(local_y / resolution)
    radius = max(0, math.ceil(clearance / resolution))
    for grid_y in range(center_y - radius, center_y + radius + 1):
        for grid_x in range(center_x - radius, center_x + radius + 1):
            if (grid_x - center_x) ** 2 + (grid_y - center_y) ** 2 > radius**2:
                continue
            if not (0 <= grid_x < width and 0 <= grid_y < height):
                return False, "目标净空范围超出地图边界"
            occupancy = int(data[grid_y * width + grid_x])
            if occupancy < 0:
                return False, "目标净空范围包含未知区域"
            if occupancy >= occupied_threshold:
                return False, "目标净空范围包含障碍物"
    return True, ""

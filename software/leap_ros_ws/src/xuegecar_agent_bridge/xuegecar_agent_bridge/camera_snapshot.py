"""相机压缩帧快照：缓存最新帧并按需保存为本地 jpeg 文件。

该模块不依赖 rclpy，订阅与回调由 Gateway 节点负责，便于在无 ROS2
环境下单测缓存、过期和落盘逻辑。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from xuegecar_agent_bridge.errors import GatewayRejected


class CameraSnapshotStore:
    """缓存最新相机帧；capture() 把最新帧写入快照目录并返回文件信息。"""

    def __init__(self, output_dir: Path | None, *, max_age: float = 5.0) -> None:
        """配置输出目录；output_dir 为空表示快照功能未启用。"""
        self._output_dir = output_dir
        self._max_age = max_age
        self._lock = Lock()
        self._latest: bytes | None = None
        self._latest_at: float | None = None

    def store(self, data: bytes, image_format: str) -> None:
        """缓存一帧压缩图片；只接受 jpeg 帧。"""
        if image_format.strip().lower() != "jpeg":
            return
        with self._lock:
            self._latest = bytes(data)
            self._latest_at = time.monotonic()

    def capture(self) -> dict[str, Any]:
        """把最新帧写入快照目录；无帧、过期或写入失败时抛出领域错误。"""
        if self._output_dir is None:
            raise GatewayRejected("UNAVAILABLE", "未配置快照目录 snapshot_dir")
        with self._lock:
            data = self._latest
            latest_at = self._latest_at
        if data is None or latest_at is None:
            raise GatewayRejected("NO_FRAME", "尚未收到相机帧")
        age = time.monotonic() - latest_at
        if age > self._max_age:
            raise GatewayRejected(
                "STALE_FRAME", f"相机帧已 {age:.1f} 秒未更新，可能已断流"
            )
        captured_at = datetime.now(timezone.utc)
        filename = "snapshot_" + captured_at.strftime("%Y%m%dT%H%M%S_%f") + ".jpg"
        path = self._output_dir / filename
        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as error:
            raise GatewayRejected(
                "SNAPSHOT_WRITE_ERROR", f"快照写入失败：{error}"
            ) from error
        return {
            "status": "captured",
            "path": str(path),
            "format": "jpeg",
            "captured_at": captured_at.isoformat(),
            "frame_age_seconds": round(age, 3),
        }


__all__ = ["CameraSnapshotStore"]

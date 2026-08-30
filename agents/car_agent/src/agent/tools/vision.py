"""图像识别 Agent 工具。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from langchain_core.tools import tool

from agent.common.robot_gateway import RobotGatewayError, get_robot_gateway
from agent.vision import VisionRecognitionError, VisionResult, get_vision_recognizer


@tool
async def recognize_image(
    image_path: str | None = None, question: str | None = None
) -> dict:
    """识别图片内容；不传 image_path 时自动抓取小车相机当前帧。"""
    try:
        target = Path(image_path) if image_path else await _camera_frame_path()
        result = await get_vision_recognizer().recognize(target, question)
    except VisionRecognitionError as error:
        return VisionResult.failure(error).as_dict()
    except RuntimeError as error:
        # 配置缺失等启动问题也以工具结果返回，避免 Supervisor 整图崩溃。
        return VisionResult.failure(
            VisionRecognitionError("CONFIGURATION_ERROR", str(error))
        ).as_dict()
    return result.as_dict()


async def _camera_frame_path() -> Path:
    """请求 Gateway 抓取相机最新帧，并返回落盘后的本地文件路径。"""
    try:
        snapshot = await asyncio.to_thread(get_robot_gateway().get_camera_snapshot)
    except RobotGatewayError as error:
        raise VisionRecognitionError(
            "SNAPSHOT_FAILED", f"抓取相机帧失败：{error}"
        ) from error
    path = snapshot.get("path")
    if not path:
        raise VisionRecognitionError(
            "SNAPSHOT_FAILED",
            f"抓取相机帧失败：{snapshot.get('error') or snapshot.get('error_code') or snapshot}",
        )
    return Path(str(path))


__all__ = ["recognize_image"]

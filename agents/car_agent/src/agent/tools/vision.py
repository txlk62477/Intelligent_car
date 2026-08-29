"""图像识别 Agent 工具。"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from agent.vision import VisionRecognitionError, VisionResult, get_vision_recognizer


@tool
async def recognize_image(image_path: str, question: str | None = None) -> dict:
    """识别允许目录中的本地图片；可选地回答关于图片的问题。"""
    try:
        result = await get_vision_recognizer().recognize(Path(image_path), question)
    except VisionRecognitionError as error:
        return VisionResult.failure(error).as_dict()
    except RuntimeError as error:
        # 配置缺失等启动问题也以工具结果返回，避免 Supervisor 整图崩溃。
        return VisionResult.failure(
            VisionRecognitionError("CONFIGURATION_ERROR", str(error))
        ).as_dict()
    return result.as_dict()


__all__ = ["recognize_image"]

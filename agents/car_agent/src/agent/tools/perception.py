"""感知查询工具与跟随任务委派 schema。"""

from __future__ import annotations

import asyncio

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from agent.common.robot_gateway import RobotGatewayError, get_robot_gateway


class FollowRequest(BaseModel):
    """Supervisor 向跟随子图交付的单个跟随请求。"""

    model_config = ConfigDict(extra="forbid")

    target_label: str = Field(
        description="单个 YOLO COCO 英文类别名，例如 person、cup、bottle"
    )
    timeout_seconds: float = Field(
        default=60.0,
        gt=0.0,
        le=300.0,
        description="跟随总时限，默认 60 秒，最大 300 秒",
    )


@tool
async def get_perception_detections() -> dict:
    """查询当前画面的 YOLO 检测结果（类别、置信度和画面位置）。"""
    try:
        detections = await asyncio.to_thread(get_robot_gateway().get_detections)
    except RobotGatewayError as error:
        return {"status": "FAILED", "error_code": error.code, "error": str(error)}
    return detections


@tool
def delegate_to_follow_workflow(request: FollowRequest) -> str:
    """把目标类别与时限委派给按需检测、候选选择和人工确认的固定子图。"""
    return "该工具由 Supervisor handoff 节点处理，不应被直接调用。"


DETECTION_TOOLS = [get_perception_detections]

__all__ = [
    "DETECTION_TOOLS",
    "FollowRequest",
    "delegate_to_follow_workflow",
    "get_perception_detections",
]

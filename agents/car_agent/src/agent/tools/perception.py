"""跟随任务委派 schema。"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field


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
def delegate_to_follow_workflow(
    target_label: Annotated[
        str,
        Field(description="单个 YOLO COCO 英文类别名，例如 person、cup、bottle"),
    ],
    timeout_seconds: Annotated[
        float,
        Field(gt=0.0, le=300.0, description="跟随总时限，默认 60 秒，最大 300 秒"),
    ] = 60.0,
) -> str:
    """把目标类别与时限委派给按需检测、候选选择和人工确认的固定子图。"""
    return "该工具由 Supervisor handoff 节点处理，不应被直接调用。"


__all__ = [
    "FollowRequest",
    "delegate_to_follow_workflow",
]

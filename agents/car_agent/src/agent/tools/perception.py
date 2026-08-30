"""按需视觉跟随任务工具。"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from langchain_core.tools import tool
from pydantic import Field

from agent.common.robot_gateway import RobotGatewayError, get_robot_gateway


@tool
def start_follow_target(
    target_label: Annotated[
        str,
        Field(description="单个标准 YOLO COCO 英文类别名，例如 person、cup、bottle"),
    ],
    timeout_seconds: Annotated[
        float,
        Field(gt=0.0, le=300.0, description="跟随总时限，默认 60 秒，最大 300 秒"),
    ] = 60.0,
) -> dict:
    """启动按需 YOLO 视觉跟随；小车会低速保持目标居中和初始框大小。"""
    operation_id = f"follow-{uuid4().hex}"
    try:
        return get_robot_gateway().submit_follow(
            {
                "operation_id": operation_id,
                "target_label": target_label.strip().lower(),
                "timeout_seconds": timeout_seconds,
            }
        )
    except RobotGatewayError as error:
        return {"status": "FAILED", "error_code": error.code, "error": str(error)}


@tool
def get_follow_task_status(operation_id: str) -> dict:
    """查询先前创建的视觉跟随任务状态和最近一次控制观测。"""
    try:
        return get_robot_gateway().get_follow(operation_id)
    except RobotGatewayError as error:
        return {"status": "FAILED", "error_code": error.code, "error": str(error)}


@tool
def cancel_follow_task(operation_id: str) -> dict:
    """取消指定视觉跟随任务并停车、关闭 YOLO。"""
    try:
        return get_robot_gateway().cancel_follow(operation_id)
    except RobotGatewayError as error:
        return {"status": "FAILED", "error_code": error.code, "error": str(error)}


FOLLOW_TOOLS = [start_follow_target, get_follow_task_status, cancel_follow_task]

__all__ = [
    "FOLLOW_TOOLS",
    "cancel_follow_task",
    "get_follow_task_status",
    "start_follow_target",
]

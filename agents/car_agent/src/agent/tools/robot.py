"""机器人状态、急停和移动委派工具定义。"""

from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.common.robot_gateway import get_robot_gateway

MotionType = Literal["forward", "backward", "turn_left", "turn_right"]
MotionMode = Literal["distance", "angle", "time"]


class MotionAction(BaseModel):
    """Supervisor 向固定移动子图交付的单个原子动作。"""

    model_config = ConfigDict(extra="forbid")

    type: MotionType = Field(description="前进、后退、左转或右转")
    mode: MotionMode = Field(description="distance 为米，angle 为度，time 为秒")
    value: float = Field(description="严格使用用户给出的正数目标量")

    @model_validator(mode="after")
    def validate_action(self) -> MotionAction:
        """确定性检查动作与范围，不允许模型偷偷截断。"""
        linear = self.type in {"forward", "backward"}
        if linear and self.mode not in {"distance", "time"}:
            raise ValueError("前进和后退只支持 distance 或 time")
        if not linear and self.mode not in {"angle", "time"}:
            raise ValueError("左转和右转只支持 angle 或 time")
        limits = {
            "distance": (0.05, 3.0, "距离"),
            "angle": (1.0, 180.0, "角度"),
            "time": (0.1, 10.0, "时间"),
        }
        minimum, maximum, label = limits[self.mode]
        if not minimum <= self.value <= maximum:
            raise ValueError(f"{label}必须在 {minimum:g} 到 {maximum:g} 之间")
        return self


@tool
def get_robot_status() -> dict:
    """查询小车在线状态、EKF 融合相对位姿、速度以及当前或最近运动结果。"""
    return get_robot_gateway().get_status()


@tool
def stop_robot() -> dict:
    """立即停止小车并取消当前运动。此工具无需人工确认。"""
    return get_robot_gateway().stop()


@tool
def delegate_to_motion_workflow(
    actions: Annotated[
        list[MotionAction],
        "按用户原始顺序排列的完整动作列表，一次委派整段计划",
    ],
) -> str:
    """把短距离、短时间或转角运动委派给需要人工确认的固定子图。"""
    return "该工具由 Supervisor handoff 节点处理，不应被直接调用。"


# 普通工具由 Supervisor 的直接工具节点执行；移动工具只作为 handoff schema。
DIRECT_TOOLS = [get_robot_status, stop_robot]
SUPERVISOR_TOOLS = [*DIRECT_TOOLS, delegate_to_motion_workflow]

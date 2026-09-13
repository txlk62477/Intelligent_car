"""Agent 与固定 Workflow 之间共享的严格业务请求模型。

这些模型是执行参数的唯一真源：Agent 只能提交结构化请求，主图编排入口和
Workflow 都会用同一套模型重新校验，未知字段一律拒绝。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MotionType = Literal["forward", "backward", "turn_left", "turn_right"]
MotionMode = Literal["distance", "angle", "time"]


class WorkflowSubmission(BaseModel):
    """校验整个请求信封，保留原始参数直到业务校验。"""

    model_config = ConfigDict(extra="forbid")

    kind: str
    arguments: dict[str, Any]
    source_observation_ids: list[str] = Field(default_factory=list, strict=True)
    step_description: str = Field(default="", max_length=200)
    remaining_goals_after_success: list[str] | None = Field(
        default=None, max_length=30, strict=True
    )

    @field_validator("step_description")
    @classmethod
    def clean_description(cls, value: str) -> str:
        """清理展示标题。"""
        return value.strip()

    @field_validator("remaining_goals_after_success")
    @classmethod
    def clean_goals(cls, value: list[str] | None) -> list[str] | None:
        """待完成目标必须是非空的短标题。"""
        if value is None:
            return None
        cleaned = [item.strip() for item in value]
        if any(not item or len(item) > 200 for item in cleaned):
            raise ValueError("剩余目标必须是非空且不超过 200 字的标题")
        return cleaned


class MotionAction(BaseModel):
    """一段相对移动计划中的单个原子动作。"""

    model_config = ConfigDict(extra="forbid")

    type: MotionType = Field(description="前进、后退、左转或右转")
    mode: MotionMode = Field(description="distance 为米，angle 为度，time 为秒")
    value: float = Field(description="严格使用用户给出的正数目标量")

    @model_validator(mode="after")
    def validate_action(self) -> MotionAction:
        """确定性检查动作与范围，不允许模型偷偷截断或换算。"""
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


class MotionRequest(BaseModel):
    """短距离相对移动请求。"""

    model_config = ConfigDict(extra="forbid")

    actions: list[MotionAction] = Field(min_length=1)


class FollowRequest(BaseModel):
    """按 YOLO 类别跟随目标的请求。"""

    model_config = ConfigDict(extra="forbid")

    target_label: str = Field(
        description="单个 YOLO COCO 英文类别名，例如 person、cup、bottle"
    )
    timeout_seconds: float = Field(
        default=60.0, gt=0.0, le=300.0, description="跟随总时限，默认 60 秒"
    )

    @field_validator("target_label")
    @classmethod
    def clean_target_label(cls, value: str) -> str:
        """统一小写并清理空白，保证与检测结果可比较。"""
        cleaned = value.strip().lower()
        if not cleaned:
            raise ValueError("目标类别不能为空")
        return cleaned


class SaveLocationRequest(BaseModel):
    """把当前位置保存为命名地点的请求。"""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        """清理地点名称。"""
        return value.strip()


class DeleteLocationRequest(BaseModel):
    """删除当前地图内某个命名地点的请求。"""

    model_config = ConfigDict(extra="forbid")

    location: str = Field(min_length=1, max_length=100)

    @field_validator("location")
    @classmethod
    def clean_location(cls, value: str) -> str:
        """清理地点名称。"""
        return value.strip()


class NavigationRequest(BaseModel):
    """前往当前地图内某个命名地点的请求。"""

    model_config = ConfigDict(extra="forbid")

    location: str = Field(min_length=1, max_length=100)
    timeout_seconds: float = Field(default=300.0, gt=0.0, le=900.0)

    @field_validator("location")
    @classmethod
    def clean_location(cls, value: str) -> str:
        """清理地点名称，导航只接受名称不接受坐标。"""
        return value.strip()


__all__ = [
    "DeleteLocationRequest",
    "FollowRequest",
    "MotionAction",
    "MotionMode",
    "MotionRequest",
    "MotionType",
    "NavigationRequest",
    "SaveLocationRequest",
]

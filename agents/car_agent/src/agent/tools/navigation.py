"""地图位置教学与 Nav2 导航 handoff 工具。"""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, field_validator


class SaveLocationRequest(BaseModel):
    """保存当前位置时 Supervisor 必须提供的结构化参数。"""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        """清理地点名称。"""
        return value.strip()


class NavigationRequest(BaseModel):
    """按当前地图位置记忆导航的结构化参数。"""

    model_config = ConfigDict(extra="forbid")

    location: str = Field(min_length=1, max_length=100)
    timeout_seconds: float = Field(default=300.0, gt=0.0, le=900.0)


@tool
def delegate_to_save_location_workflow(
    label: Annotated[str, "要赋予当前位置的明确名称，例如书桌前"],
    aliases: Annotated[list[str], "可选别名，不得编造用户未表达的名称"] | None = None,
) -> str:
    """把“记住这里”委派给需要定位校验和人工确认的位置教学 Workflow。"""
    return "该工具由 Supervisor handoff 节点处理，不应被直接调用。"


@tool
def delegate_to_navigation_workflow(
    location: Annotated[str, "用户要求前往的地点名称，不得自行生成坐标"],
    timeout_seconds: Annotated[float, "导航最长时间，默认 300 秒，最大 900 秒"] = 300.0,
) -> str:
    """把地点名称委派给当前地图隔离、路径预检和人工确认的 Nav2 Workflow。"""
    return "该工具由 Supervisor handoff 节点处理，不应被直接调用。"


@tool
def delegate_to_delete_location_workflow(
    location: Annotated[str, "要从当前地图删除的地点名称"],
) -> str:
    """把删除当前地图位置记忆委派给需要人工确认的 Workflow。"""
    return "该工具由 Supervisor handoff 节点处理，不应被直接调用。"

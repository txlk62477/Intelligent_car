"""固定 Workflow 的静态接入描述；不授予执行或确认权限。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from agent.tools.requests import (
    DeleteLocationRequest,
    FollowRequest,
    MotionRequest,
    NavigationRequest,
    SaveLocationRequest,
)


@dataclass(frozen=True)
class WorkflowSpec:
    """登记参数模型、输入准备器、调度节点和结果字段。"""

    request_model: type[BaseModel]
    prepare: Callable[[dict[str, Any], str], dict[str, Any]]
    node: str
    result_field: str
    title: str

    def validate(self, raw: Any) -> dict[str, Any]:
        """拒绝非对象输入，并用业务模型校验全部字段。"""
        if not isinstance(raw, dict):
            raise ValueError("arguments 必须是对象")
        return self.request_model.model_validate(raw).model_dump()

    def result(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """读取结果；缺失时保守失败，不回落到另一种 Workflow。"""
        raw = state.get(self.result_field)
        if not isinstance(raw, dict) or not raw.get("status"):
            return {"status": "failed", "summary": f"{self.title} Workflow 未返回结果"}
        return dict(raw)


def _motion(arguments: dict[str, Any], step_id: str) -> dict[str, Any]:
    return {
        "motion_actions": arguments["actions"],
        "motion_plan_id": step_id,
        "motion_action_index": 0,
        "motion_action_results": [],
        "motion_status": "delegated",
        "motion_error": "",
        "motion_result": None,
    }


def _follow(arguments: dict[str, Any], step_id: str) -> dict[str, Any]:
    return {
        "follow_target_label": arguments["target_label"],
        "follow_timeout_seconds": arguments["timeout_seconds"],
        "follow_plan_id": step_id,
        "follow_selected_from_list": False,
        "follow_resolve_attempts": 0,
        "follow_candidates": [],
        "follow_status": "delegated",
        "follow_error": "",
        "follow_observation": None,
        "follow_result": None,
    }


def _location(
    action: str, label: str, aliases: list[str], step_id: str
) -> dict[str, Any]:
    return {
        "location_action": action,
        "location_query": label,
        "location_label": label,
        "location_aliases": aliases,
        "location_plan_id": step_id,
        "location_status": "delegated",
        "location_error": "",
        "location_existing": None,
        "location_selected": None,
        "location_candidates": [],
        "location_result": None,
    }


def _save_location(arguments: dict[str, Any], step_id: str) -> dict[str, Any]:
    return _location("save", arguments["label"], arguments["aliases"], step_id)


def _delete_location(arguments: dict[str, Any], step_id: str) -> dict[str, Any]:
    return _location("delete", arguments["location"], [], step_id)


def _navigation(arguments: dict[str, Any], step_id: str) -> dict[str, Any]:
    return {
        "location_query": arguments["location"],
        "navigation_timeout_seconds": arguments["timeout_seconds"],
        "navigation_plan_id": step_id,
        "navigation_status": "delegated",
        "navigation_error": "",
        "navigation_result": None,
    }


WORKFLOW_SPECS: Mapping[str, WorkflowSpec] = MappingProxyType(
    {
        "motion": WorkflowSpec(
            MotionRequest,
            _motion,
            "relative_motion_workflow",
            "motion_result",
            "相对移动",
        ),
        "follow": WorkflowSpec(
            FollowRequest, _follow, "follow_workflow", "follow_result", "跟随目标"
        ),
        "save_location": WorkflowSpec(
            SaveLocationRequest,
            _save_location,
            "map_location_workflow",
            "location_result",
            "保存地点",
        ),
        "delete_location": WorkflowSpec(
            DeleteLocationRequest,
            _delete_location,
            "map_location_workflow",
            "location_result",
            "删除地点",
        ),
        "navigation": WorkflowSpec(
            NavigationRequest,
            _navigation,
            "map_navigation_workflow",
            "navigation_result",
            "地点导航",
        ),
    }
)


def workflow_spec(kind: str) -> WorkflowSpec:
    """按类型查表，未知类型明确拒绝。"""
    try:
        return WORKFLOW_SPECS[kind]
    except KeyError as error:
        raise ValueError(f"未知 Workflow 类型：{kind}") from error

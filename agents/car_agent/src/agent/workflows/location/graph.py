"""当前位置采样、确认并写入官方 Store 的固定 Workflow。"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from typing import Any, TypedDict
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.types import interrupt

from agent.common.robot_gateway import (
    RobotGateway,
    RobotGatewayError,
    get_robot_gateway,
)
from agent.memory.identity import resolve_memory_scope
from agent.memory.locations import LocationStore, MapPose
from agent.state.car_agent import CarAgentState, LocationResult
from agent.workflows.motion.graph import is_confirmed


class LocationWorkflowInput(TypedDict):
    """位置 Workflow 的外部输入。"""

    location_action: str
    location_label: str
    location_aliases: list[str]


class LocationWorkflowOutput(TypedDict):
    """位置 Workflow 的唯一公共输出。"""

    location_result: LocationResult


class LocationWorkflowNodes:
    """隐藏 Gateway 采样、Store namespace 和中断后重检。"""

    def __init__(self, gateway_factory: Callable[[], RobotGateway]) -> None:
        """保存 Gateway adapter 工厂。"""
        self._gateway_factory = gateway_factory

    async def prepare(
        self,
        state: CarAgentState,
        config: RunnableConfig,
        runtime: Runtime[Any],
    ) -> dict[str, Any]:
        """采样当前地图位姿并查找同名旧位置。"""
        action = str(state.get("location_action") or "save")
        label = str(state.get("location_label") or "").strip()
        aliases = [str(item).strip() for item in state.get("location_aliases", [])]
        if action not in {"save", "delete"} or not label:
            return {"location_status": "failed", "location_error": "位置请求无效"}
        if runtime.store is None:
            return {
                "location_status": "failed",
                "location_error": "当前运行环境没有提供 LangGraph Store",
            }
        try:
            map_status = await asyncio.to_thread(
                self._gateway_factory().get_navigation_status
            )
            map_id, _pose = _validated_map_pose(map_status)
            scope = resolve_memory_scope(config, runtime)
            locations = LocationStore(
                runtime.store, robot_id=scope.robot_id, map_id=map_id
            )
            matches = await locations.resolve(label)
            exact = next(
                (
                    item
                    for item in matches
                    if item.label.strip().lower() == label.lower()
                    or label.lower() in {alias.lower() for alias in item.aliases}
                ),
                None,
            )
        except (RobotGatewayError, ValueError) as error:
            return {"location_status": "failed", "location_error": str(error)}
        if action == "delete" and exact is None:
            return {
                "location_status": "failed",
                "location_error": f"当前地图没有位置 {label!r}",
                "location_map_status": map_status,
            }
        return {
            "location_label": label,
            "location_aliases": aliases,
            "location_plan_id": str(state.get("location_plan_id") or uuid4()),
            "location_map_status": map_status,
            "location_existing": None
            if exact is None
            else exact.model_dump(mode="json"),
            "location_status": "awaiting_confirmation",
            "location_error": "",
        }

    def confirm(self, state: CarAgentState) -> dict[str, Any]:
        """保存和删除都必须经过独立人工确认。"""
        action = str(state.get("location_action") or "save")
        label = str(state.get("location_label") or "")
        status = dict(state.get("location_map_status") or {})
        pose = dict(status.get("pose") or {})
        existing = state.get("location_existing")
        if action == "delete":
            message = f"准备从当前地图删除位置“{label}”，是否确认？"
        else:
            message = (
                f"准备在地图 {status.get('map_name') or status.get('map_id')} 中记录“{label}”："
                f"x={float(pose.get('x', 0.0)):.3f}, y={float(pose.get('y', 0.0)):.3f}, "
                f"yaw={math.degrees(float(pose.get('yaw', 0.0))):.1f}°。"
            )
            if isinstance(existing, dict):
                old = dict(existing.get("pose") or {})
                distance = math.hypot(
                    float(pose.get("x", 0.0)) - float(old.get("x", 0.0)),
                    float(pose.get("y", 0.0)) - float(old.get("y", 0.0)),
                )
                message += f"这将更新旧位置，坐标移动 {distance:.3f} 米。"
            message += "是否确认？"
        answer = interrupt(
            {
                "type": "confirm_map_location_change",
                "action": action,
                "message": message,
                "label": label,
                "map_id": status.get("map_id"),
                "pose": pose,
                "existing": existing,
                "confirmation_hint": '回复确认，或传入 {"confirmed": true}',
            }
        )
        if is_confirmed(answer):
            return {"location_status": "committing"}
        return {"location_status": "cancelled", "location_error": "用户未确认位置变更"}

    async def commit(
        self,
        state: CarAgentState,
        config: RunnableConfig,
        runtime: Runtime[Any],
    ) -> dict[str, Any]:
        """恢复后重检地图和位姿，再执行 Store 变更。"""
        if runtime.store is None:
            return {"location_status": "failed", "location_error": "Store 不可用"}
        original = dict(state.get("location_map_status") or {})
        try:
            current = await asyncio.to_thread(
                self._gateway_factory().get_navigation_status
            )
            map_id, current_pose = _validated_map_pose(current)
            if map_id != str(original.get("map_id") or ""):
                raise ValueError("确认期间活动地图发生变化，原确认已作废")
            scope = resolve_memory_scope(config, runtime)
            locations = LocationStore(
                runtime.store, robot_id=scope.robot_id, map_id=map_id
            )
            label = str(state.get("location_label") or "")
            action = str(state.get("location_action") or "save")
            if action == "delete":
                deleted = await locations.delete(label)
                if not deleted:
                    raise ValueError("待删除位置已不存在")
                result_location = None
            else:
                original_pose = MapPose.model_validate(original.get("pose"))
                if _pose_changed(original_pose, current_pose):
                    raise ValueError("确认期间小车位置发生明显变化，请重新记录")
                thread_id, run_id = _execution_ids(runtime)
                result_location = await locations.save(
                    label=label,
                    aliases=list(state.get("location_aliases", [])),
                    pose=current_pose,
                    map_name=str(current.get("map_name") or ""),
                    user_id=scope.user_id,
                    thread_id=thread_id,
                    run_id=run_id,
                )
        except (RobotGatewayError, ValueError) as error:
            return {"location_status": "failed", "location_error": str(error)}
        return {
            "location_status": "success",
            "location_error": "",
            "location_selected": (
                None
                if result_location is None
                else result_location.model_dump(mode="json")
            ),
        }

    def finish(self, state: CarAgentState) -> dict[str, LocationResult]:
        """把内部状态压缩成 Supervisor Tool 结果。"""
        status = str(state.get("location_status") or "failed")
        action = str(state.get("location_action") or "save")
        label = str(state.get("location_label") or "")
        if status == "success":
            verb = "删除" if action == "delete" else "记录"
            summary = f"已在当前地图{verb}位置“{label}”。"
        elif status == "cancelled":
            summary = "用户未确认，位置记忆没有变化。"
        else:
            summary = f"位置记忆变更失败：{state.get('location_error') or '未知错误'}"
        return {
            "location_result": LocationResult(
                status=status,
                summary=summary,
                action=action,
                location=state.get("location_selected"),
            )
        }


def build_location_workflow(
    *,
    gateway_factory: Callable[[], RobotGateway] = get_robot_gateway,
    name: str = "map_location_workflow",
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
):
    """构建地图位置教学/删除子图。"""
    nodes = LocationWorkflowNodes(gateway_factory)
    builder = StateGraph(
        CarAgentState,
        input_schema=LocationWorkflowInput,
        output_schema=LocationWorkflowOutput,
    )
    builder.add_node("prepare", nodes.prepare)  # type: ignore[call-overload,arg-type]
    builder.add_node("confirm", nodes.confirm)
    builder.add_node("commit", nodes.commit)  # type: ignore[call-overload,arg-type]
    builder.add_node("finish", nodes.finish)
    builder.add_edge(START, "prepare")
    builder.add_conditional_edges(
        "prepare",
        lambda state: (
            "confirm"
            if state.get("location_status") == "awaiting_confirmation"
            else "finish"
        ),
        {"confirm": "confirm", "finish": "finish"},
    )
    builder.add_conditional_edges(
        "confirm",
        lambda state: (
            "commit" if state.get("location_status") == "committing" else "finish"
        ),
        {"commit": "commit", "finish": "finish"},
    )
    builder.add_edge("commit", "finish")
    builder.add_edge("finish", END)
    return builder.compile(name=name, checkpointer=checkpointer, store=store)


def _validated_map_pose(status: dict[str, Any]) -> tuple[str, MapPose]:
    if str(status.get("status") or "") != "READY":
        raise ValueError(str(status.get("error") or "地图或 AMCL 尚未就绪"))
    map_id = str(status.get("map_id") or "").strip()
    if not map_id:
        raise ValueError("当前地图缺少 map_id")
    return map_id, MapPose.model_validate(status.get("pose"))


def _pose_changed(before: MapPose, after: MapPose) -> bool:
    distance = math.hypot(after.x - before.x, after.y - before.y)
    yaw_delta = abs(
        math.atan2(math.sin(after.yaw - before.yaw), math.cos(after.yaw - before.yaw))
    )
    return distance > 0.10 or yaw_delta > math.radians(10.0)


def _execution_ids(runtime: Runtime[Any]) -> tuple[str, str]:
    info = runtime.execution_info
    return (
        str(getattr(info, "thread_id", None) or "unknown-thread"),
        str(getattr(info, "run_id", None) or uuid4()),
    )

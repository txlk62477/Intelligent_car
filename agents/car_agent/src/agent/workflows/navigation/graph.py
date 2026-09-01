"""当前地图位置解析、路径预检、确认和 NavigateToPose Workflow。"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Callable, Mapping
from typing import Any, Literal, TypedDict
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from agent.common.robot_gateway import (
    RobotGateway,
    RobotGatewayError,
    get_robot_gateway,
)
from agent.memory.identity import resolve_memory_scope
from agent.memory.locations import LocationStore, MapLocation
from agent.state.car_agent import CarAgentState, NavigationResult
from agent.workflows.location.graph import _validated_map_pose
from agent.workflows.motion.graph import is_confirmed

TERMINAL_NAVIGATION_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}


class NavigationWorkflowInput(TypedDict):
    """地点导航 Workflow 的外部输入。"""

    location_query: str
    navigation_timeout_seconds: float


class NavigationWorkflowOutput(TypedDict):
    """地点导航 Workflow 的唯一公共输出。"""

    navigation_result: NavigationResult


class NavigationWorkflowNodes:
    """隐藏位置召回、路径预检、确认和 Nav2 轮询。"""

    def __init__(self, gateway_factory: Callable[[], RobotGateway]) -> None:
        """保存 Gateway adapter 和轮询配置。"""
        self._gateway_factory = gateway_factory
        self._poll_interval = float(os.getenv("NAVIGATION_POLL_INTERVAL", "0.5"))

    async def resolve(
        self,
        state: CarAgentState,
        config: RunnableConfig,
        runtime: Runtime[Any],
    ) -> dict[str, Any]:
        """只在当前 robot_id + map_id namespace 解析地点。"""
        query = str(state.get("location_query") or "").strip()
        try:
            timeout = float(state.get("navigation_timeout_seconds") or 300.0)
        except (TypeError, ValueError):
            timeout = 0.0
        if not query or not 0.0 < timeout <= 900.0:
            return {"navigation_status": "failed", "navigation_error": "导航请求无效"}
        if runtime.store is None:
            return {"navigation_status": "failed", "navigation_error": "Store 不可用"}
        try:
            map_status = await asyncio.to_thread(
                self._gateway_factory().get_navigation_status
            )
            map_id, _pose = _validated_map_pose(map_status)
            scope = resolve_memory_scope(config, runtime)
            locations = LocationStore(
                runtime.store, robot_id=scope.robot_id, map_id=map_id
            )
            matches = await locations.resolve(query)
        except (RobotGatewayError, ValueError) as error:
            return {"navigation_status": "failed", "navigation_error": str(error)}
        if not matches:
            return {
                "navigation_status": "failed",
                "navigation_error": f"当前地图尚未记录位置“{query}”",
                "location_map_status": map_status,
            }
        values = [item.model_dump(mode="json") for item in matches]
        return {
            "location_map_status": map_status,
            "location_candidates": values,
            "location_selected": values[0] if len(values) == 1 else None,
            "navigation_timeout_seconds": timeout,
            "navigation_plan_id": str(state.get("navigation_plan_id") or uuid4()),
            "navigation_status": "preflighting" if len(values) == 1 else "selecting",
            "navigation_error": "",
        }

    def select(self, state: CarAgentState) -> dict[str, Any]:
        """多个候选时中断并要求用户明确选择。"""
        candidates = [dict(item) for item in state.get("location_candidates", [])]
        answer = interrupt(
            {
                "type": "select_map_location",
                "message": "当前地图中有多个相似位置，请回复序号选择或取消。",
                "candidates": [
                    {
                        "index": index,
                        "label": item.get("label"),
                        "aliases": item.get("aliases", []),
                        "pose": item.get("pose"),
                    }
                    for index, item in enumerate(candidates, start=1)
                ],
            }
        )
        if isinstance(answer, Mapping):
            answer = answer.get("index", answer.get("answer", ""))
        normalized = str(answer or "").strip().lower()
        if normalized in {"取消", "cancel", "no", "n"}:
            return {
                "navigation_status": "cancelled",
                "navigation_error": "用户取消地点选择",
            }
        try:
            index = int(normalized) - 1
        except ValueError:
            return {"navigation_status": "failed", "navigation_error": "地点选择无效"}
        if not 0 <= index < len(candidates):
            return {
                "navigation_status": "failed",
                "navigation_error": "地点序号超出范围",
            }
        return {
            "location_selected": candidates[index],
            "navigation_status": "preflighting",
            "navigation_error": "",
        }

    async def preflight(self, state: CarAgentState) -> dict[str, Any]:
        """调用 Gateway 做静态目标和 ComputePathToPose 检查。"""
        selected = dict(state.get("location_selected") or {})
        map_status = dict(state.get("location_map_status") or {})
        try:
            location = MapLocation.model_validate(selected)
            if location.map_id != str(map_status.get("map_id") or ""):
                raise ValueError("位置不属于当前地图")
            result = await asyncio.to_thread(
                self._gateway_factory().preflight_navigation,
                {"map_id": location.map_id, "pose": location.pose.model_dump()},
            )
            if str(result.get("status") or "") != "READY":
                raise ValueError(str(result.get("error") or "Nav2 路径预检失败"))
        except (RobotGatewayError, ValueError) as error:
            return {"navigation_status": "failed", "navigation_error": str(error)}
        return {
            "navigation_status": "awaiting_confirmation",
            "navigation_error": "",
            "navigation_operation": result,
        }

    def confirm(self, state: CarAgentState) -> dict[str, Any]:
        """展示当前地图和存储坐标后请求导航确认。"""
        location = MapLocation.model_validate(state.get("location_selected"))
        answer = interrupt(
            {
                "type": "confirm_map_navigation",
                "message": (
                    f"将在当前地图导航到“{location.label}”：x={location.pose.x:.3f}, "
                    f"y={location.pose.y:.3f}, yaw={math.degrees(location.pose.yaw):.1f}°。"
                    "路径预检已通过，是否开始？"
                ),
                "label": location.label,
                "map_id": location.map_id,
                "pose": location.pose.model_dump(),
                "needs_review": location.needs_review,
                "confirmation_hint": '回复确认执行，或传入 {"confirmed": true}',
            }
        )
        if is_confirmed(answer):
            return {"navigation_status": "executing"}
        return {
            "navigation_status": "cancelled",
            "navigation_error": "用户未确认导航任务",
        }

    async def execute(
        self,
        state: CarAgentState,
        config: RunnableConfig,
        runtime: Runtime[Any],
    ) -> dict[str, Any]:
        """重检地图后提交 Nav2 goal，等待终态并更新使用统计。"""
        gateway = self._gateway_factory()
        location = MapLocation.model_validate(state.get("location_selected"))
        operation_id = f"nav-{state.get('navigation_plan_id') or uuid4()}"
        timeout = float(state.get("navigation_timeout_seconds") or 300.0)
        submitted = False
        try:
            status = await asyncio.to_thread(gateway.get_navigation_status)
            map_id, _pose = _validated_map_pose(status)
            if map_id != location.map_id:
                raise RobotGatewayError("MAP_CHANGED", "确认期间活动地图发生变化")
            current = await asyncio.to_thread(
                gateway.submit_navigation,
                {
                    "operation_id": operation_id,
                    "map_id": location.map_id,
                    "pose": location.pose.model_dump(),
                    "timeout_seconds": timeout,
                },
            )
            submitted = True
            deadline = asyncio.get_running_loop().time() + timeout
            while str(current.get("status")) not in TERMINAL_NAVIGATION_STATUSES:
                if asyncio.get_running_loop().time() >= deadline:
                    await asyncio.to_thread(gateway.cancel_navigation, operation_id)
                    await asyncio.to_thread(gateway.stop)
                    current = {
                        **current,
                        "status": "TIMED_OUT",
                        "error_code": "WORKFLOW_TIMEOUT",
                        "error": "导航超过允许时间，已取消并停车",
                    }
                    break
                await asyncio.sleep(self._poll_interval)
                current = await asyncio.to_thread(gateway.get_navigation, operation_id)
        except asyncio.CancelledError:
            if submitted:
                await asyncio.to_thread(gateway.stop)
            raise
        except RobotGatewayError as error:
            if submitted:
                try:
                    await asyncio.to_thread(gateway.stop)
                except RobotGatewayError:
                    pass
            current = {
                "operation_id": operation_id,
                "status": "FAILED",
                "error_code": error.code,
                "error": str(error),
            }
        if runtime.store is not None:
            scope = resolve_memory_scope(config, runtime)
            locations = LocationStore(
                runtime.store,
                robot_id=scope.robot_id,
                map_id=location.map_id,
            )
            try:
                updated = await locations.record_result(
                    location, str(current.get("status"))
                )
                location = updated
            except Exception:
                pass
        success = str(current.get("status")) == "SUCCEEDED"
        return {
            "navigation_status": "success" if success else "failed",
            "navigation_error": ""
            if success
            else str(current.get("error") or current.get("status")),
            "navigation_operation": dict(current),
            "location_selected": location.model_dump(mode="json"),
        }

    def finish(self, state: CarAgentState) -> dict[str, NavigationResult]:
        """压缩导航内部状态。"""
        status = str(state.get("navigation_status") or "failed")
        selected = state.get("location_selected")
        label = str(selected.get("label") if isinstance(selected, dict) else "目标位置")
        if status == "success":
            summary = f"已通过 Nav2 到达“{label}”。"
        elif status == "cancelled":
            summary = "用户未确认，导航任务已取消。"
        else:
            summary = f"导航失败：{state.get('navigation_error') or '未知错误'}"
        return {
            "navigation_result": NavigationResult(
                status=status,
                summary=summary,
                location=selected if isinstance(selected, dict) else None,
                final_observation=state.get("navigation_operation"),
            )
        }


def build_navigation_workflow(
    *,
    gateway_factory: Callable[[], RobotGateway] = get_robot_gateway,
    name: str = "map_navigation_workflow",
    checkpointer: BaseCheckpointSaver | None = None,
):
    """构建地点解析和 Nav2 导航子图。"""
    nodes = NavigationWorkflowNodes(gateway_factory)
    builder = StateGraph(
        CarAgentState,
        input_schema=NavigationWorkflowInput,
        output_schema=NavigationWorkflowOutput,
    )
    for name_, node in (
        ("resolve", nodes.resolve),
        ("select", nodes.select),
        ("preflight", nodes.preflight),
        ("confirm", nodes.confirm),
        ("execute", nodes.execute),
        ("finish", nodes.finish),
    ):
        builder.add_node(name_, node)
    builder.add_edge(START, "resolve")
    builder.add_conditional_edges(
        "resolve",
        _route_status,
        {"select": "select", "preflight": "preflight", "finish": "finish"},
    )
    builder.add_conditional_edges(
        "select",
        _route_status,
        {"preflight": "preflight", "finish": "finish"},
    )
    builder.add_conditional_edges(
        "preflight",
        _route_status,
        {"confirm": "confirm", "finish": "finish"},
    )
    builder.add_conditional_edges(
        "confirm",
        _route_status,
        {"execute": "execute", "finish": "finish"},
    )
    builder.add_edge("execute", "finish")
    builder.add_edge("finish", END)
    return builder.compile(name=name, checkpointer=checkpointer)


def _route_status(
    state: CarAgentState,
) -> Literal["select", "preflight", "confirm", "execute", "finish"]:
    return {
        "selecting": "select",
        "preflighting": "preflight",
        "awaiting_confirmation": "confirm",
        "executing": "execute",
    }.get(str(state.get("navigation_status")), "finish")

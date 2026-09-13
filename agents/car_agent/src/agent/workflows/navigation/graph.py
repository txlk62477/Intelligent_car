"""当前地图位置解析、路径预检、确认和 NavigateToPose Workflow。"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Callable, Mapping
from typing import Any, Literal, TypedDict, cast
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore
from langgraph.types import interrupt
from typing_extensions import NotRequired

from agent.common.robot_gateway import (
    RobotGateway,
    RobotGatewayError,
    get_robot_gateway,
)
from agent.memory.identity import resolve_memory_scope
from agent.memory.locations import LocationStore, MapLocation
from agent.state.car_agent import CarAgentState, NavigationResult, WorkflowStatus
from agent.workflows.location.graph import _validated_map_pose
from agent.workflows.motion.graph import is_confirmed
from agent.workflows.recovery import (
    UNKNOWN_STATUS,
    best_effort,
    is_pending,
    not_submitted_record,
    recover_submission,
    unknown_record,
)

TERMINAL_NAVIGATION_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}


class NavigationWorkflowInput(TypedDict):
    """地点导航 Workflow 的外部输入。"""

    location_query: str
    navigation_timeout_seconds: float
    # 由主图按步骤下发：同一个 step_id 恢复时复用同一个 operation_id。
    navigation_plan_id: NotRequired[str]


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
        payload = {
            "operation_id": operation_id,
            "map_id": location.map_id,
            "pose": location.pose.model_dump(),
            "timeout_seconds": timeout,
        }
        try:
            current = await self._submit(
                gateway, payload, operation_id, location.map_id
            )
            if is_pending(current, TERMINAL_NAVIGATION_STATUSES):
                current = await self._await_terminal(
                    gateway, payload, operation_id, current, timeout
                )
        except asyncio.CancelledError:
            # 运行被外部取消：goal 可能已经下发，先尽力停车再向上抛出取消。
            await best_effort(gateway.stop)
            raise
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
        outcome = _navigation_outcome(current)
        return {
            "navigation_status": outcome,
            "navigation_error": ""
            if outcome == "success"
            else str(current.get("error") or current.get("status")),
            "navigation_operation": dict(current),
            "location_selected": location.model_dump(mode="json"),
        }

    async def _submit(
        self,
        gateway: RobotGateway,
        payload: dict[str, Any],
        operation_id: str,
        expected_map_id: str,
    ) -> dict[str, Any]:
        """确认后重检地图并提交 goal；提交结果未知时按同一编号查询。"""
        try:
            status = await asyncio.to_thread(gateway.get_navigation_status)
            map_id, _pose = _validated_map_pose(status)
        except (RobotGatewayError, ValueError) as error:
            return not_submitted_record(
                payload=payload,
                error_code="MAP_CHECK_FAILED",
                error=f"确认后无法确认当前地图：{error}",
            )
        if map_id != expected_map_id:
            return not_submitted_record(
                payload=payload,
                error_code="MAP_CHANGED",
                error="确认期间活动地图发生变化，已取消本次导航",
            )
        try:
            return dict(await asyncio.to_thread(gateway.submit_navigation, payload))
        except RobotGatewayError as error:
            recovery = await asyncio.to_thread(
                recover_submission,
                getter=gateway.get_navigation,
                operation_id=operation_id,
                error=error,
            )
            if recovery["resolution"] == "recovered":
                return dict(recovery["record"] or {})
            if recovery["resolution"] == "not_found":
                return not_submitted_record(
                    payload=payload,
                    error_code=recovery["error_code"],
                    error=recovery["error"],
                )
            await best_effort(gateway.stop)
            return unknown_record(
                operation_id=operation_id,
                error_code=recovery["error_code"],
                error=recovery["error"],
            )

    async def _await_terminal(
        self,
        gateway: RobotGateway,
        payload: dict[str, Any],
        operation_id: str,
        current: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        """轮询到终态；超时先取消并停车，查询失败按状态未知处理。"""
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            while is_pending(current, TERMINAL_NAVIGATION_STATUSES):
                if asyncio.get_running_loop().time() >= deadline:
                    await best_effort(gateway.cancel_navigation, operation_id)
                    await best_effort(gateway.stop)
                    return {
                        **current,
                        "status": "TIMED_OUT",
                        "error_code": "WORKFLOW_TIMEOUT",
                        "error": "导航超过允许时间，已取消并停车",
                    }
                await asyncio.sleep(self._poll_interval)
                current = dict(
                    await asyncio.to_thread(gateway.get_navigation, operation_id)
                )
            return current
        except RobotGatewayError as error:
            await best_effort(gateway.stop)
            return unknown_record(
                operation_id=operation_id,
                error_code=str(error.code or "UNAVAILABLE"),
                error=f"导航任务已提交但状态无法确认：{error}",
            )

    def finish(self, state: CarAgentState) -> dict[str, NavigationResult]:
        """压缩导航内部状态。"""
        status = str(state.get("navigation_status") or "failed")
        selected = state.get("location_selected")
        label = str(selected.get("label") if isinstance(selected, dict) else "目标位置")
        if status == "success":
            summary = f"已通过 Nav2 到达“{label}”。"
        elif status == "cancelled":
            summary = "导航任务已取消。"
        elif status == "execution_unknown":
            summary = (
                "导航执行状态未知：目标已提交但无法确认结果。"
                "请先确认小车位置，不要直接重试。"
            )
        else:
            summary = f"导航失败：{state.get('navigation_error') or '未知错误'}"
        return {
            "navigation_result": NavigationResult(
                status=cast(WorkflowStatus, status),
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
    store: BaseStore | None = None,
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
        builder.add_node(name_, node)  # type: ignore[arg-type]
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
    return builder.compile(name=name, checkpointer=checkpointer, store=store)


def _navigation_outcome(record: Mapping[str, Any]) -> str:
    """把 Gateway 记录归一化为导航终态。"""
    status = str(record.get("status") or "")
    if status == "SUCCEEDED":
        return "success"
    if status == "CANCELLED":
        return "cancelled"
    if status == UNKNOWN_STATUS:
        return UNKNOWN_STATUS
    return "failed"


def _route_status(
    state: CarAgentState,
) -> Literal["select", "preflight", "confirm", "execute", "finish"]:
    destination = {
        "selecting": "select",
        "preflighting": "preflight",
        "awaiting_confirmation": "confirm",
        "executing": "execute",
    }.get(str(state.get("navigation_status")), "finish")
    return cast(
        Literal["select", "preflight", "confirm", "execute", "finish"],
        destination,
    )

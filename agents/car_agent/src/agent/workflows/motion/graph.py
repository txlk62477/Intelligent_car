"""人工确认后逐条调用 Robot Gateway 的相对移动子图。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from typing import Any, Literal
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent.common.robot_gateway import (
    RobotGateway,
    RobotGatewayError,
    get_robot_gateway,
)
from agent.state.car_agent import CarAgentState, MotionResult
from agent.tools.robot import MotionAction

TERMINAL_STATUSES = {
    "SUCCEEDED",
    "FAILED",
    "TIMED_OUT",
    "CANCELLED",
    "ODOM_TIMEOUT",
}
ACTION_LABELS = {
    "forward": "前进",
    "backward": "后退",
    "turn_left": "左转",
    "turn_right": "右转",
}
MODE_UNITS = {"distance": "米", "angle": "度", "time": "秒"}


class MotionWorkflowNodes:
    """把校验、确认、串行等待和错误归一化隐藏在子图 interface 后。"""

    def __init__(self, gateway_factory: Callable[[], RobotGateway]) -> None:
        """保存 Gateway 工厂并从环境变量读取轮询参数。"""
        self._gateway_factory = gateway_factory
        self._poll_interval = float(os.getenv("MOTION_POLL_INTERVAL", "0.25"))
        self._action_timeout = float(os.getenv("MOTION_ACTION_TIMEOUT", "60.0"))

    def initialize(self, state: CarAgentState) -> dict[str, Any]:
        """再次确定性校验 handoff 参数并初始化可恢复状态。"""
        try:
            actions = [
                MotionAction.model_validate(action).model_dump()
                for action in state.get("motion_actions", [])
            ]
            if not actions:
                raise ValueError("动作列表不能为空")
        except (TypeError, ValueError) as error:
            return {
                "motion_status": "failed",
                "motion_error": f"动作计划无效：{error}",
                "motion_action_results": [],
            }
        return {
            "motion_actions": actions,
            "motion_plan_id": str(state.get("motion_plan_id") or uuid4()),
            "motion_action_index": 0,
            "motion_action_results": [],
            "motion_status": "awaiting_confirmation",
            "motion_error": "",
            "motion_result": None,
        }

    def confirm(self, state: CarAgentState) -> dict[str, Any]:
        """整段计划只请求一次人工确认。"""
        actions = list(state.get("motion_actions", []))
        answer = interrupt(
            {
                "type": "confirm_robot_motion",
                "message": (
                    "小车将按顺序执行以下动作。第一版不使用雷达避障，"
                    "请确保周围安全后确认。"
                ),
                "actions": actions,
                "summary": format_plan(actions),
                "confirmation_hint": '回复确认执行，或传入 {"confirmed": true}',
            }
        )
        if is_confirmed(answer):
            return {"motion_status": "executing"}
        return {
            "motion_status": "cancelled",
            "motion_error": "用户未确认运动计划",
        }

    async def execute_next(self, state: CarAgentState) -> dict[str, Any]:
        """提交当前原子动作并等待终态，不在 Graph 内发布速度。"""
        index = int(state.get("motion_action_index", 0))
        actions = list(state.get("motion_actions", []))
        action = dict(actions[index])
        operation_id = f"{state['motion_plan_id']}:{index}"
        payload = {"operation_id": operation_id, **action}
        gateway = self._gateway_factory()
        submitted = False
        try:
            current = await asyncio.to_thread(gateway.submit_motion, payload)
            submitted = True
            deadline = asyncio.get_running_loop().time() + self._action_timeout
            while str(current.get("status")) not in TERMINAL_STATUSES:
                if asyncio.get_running_loop().time() >= deadline:
                    await asyncio.to_thread(gateway.stop)
                    current = {
                        **payload,
                        "status": "TIMED_OUT",
                        "error_code": "WORKFLOW_TIMEOUT",
                        "error": "60 秒内未收到动作终态，已下发停止",
                    }
                    break
                await asyncio.sleep(self._poll_interval)
                current = await asyncio.to_thread(gateway.get_motion, operation_id)
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
                **payload,
                "status": "FAILED",
                "error_code": error.code,
                "error": str(error),
            }

        results = [*state.get("motion_action_results", []), dict(current)]
        if current.get("status") == "SUCCEEDED":
            next_index = index + 1
            return {
                "motion_action_results": results,
                "motion_action_index": next_index,
                "motion_status": "success"
                if next_index >= len(actions)
                else "executing",
                "motion_error": "",
            }
        return {
            "motion_action_results": results,
            "motion_status": "failed",
            "motion_error": str(
                current.get("error") or current.get("status") or "动作失败"
            ),
        }

    def finish(self, state: CarAgentState) -> dict[str, MotionResult]:
        """把内部执行状态压缩成 Supervisor 可使用的结构化结果。"""
        status = str(state.get("motion_status", "failed"))
        results = [dict(item) for item in state.get("motion_action_results", [])]
        if status == "success":
            summary = f"运动计划执行完成，共完成 {len(results)} 个动作。"
            failed_action = None
        elif status == "cancelled":
            summary = "用户未确认，运动计划已取消，小车没有移动。"
            failed_action = None
        else:
            failed_action = results[-1] if results else None
            summary = f"运动计划执行失败：{state.get('motion_error') or '未知错误'}"
        return {
            "motion_result": MotionResult(
                status=status,
                summary=summary,
                completed_actions=[
                    item for item in results if item.get("status") == "SUCCEEDED"
                ],
                failed_action=failed_action,
            )
        }


def build_motion_workflow(
    *,
    gateway_factory: Callable[[], RobotGateway] = get_robot_gateway,
    name: str = "relative_motion_workflow",
    checkpointer: BaseCheckpointSaver | None = None,
):
    """构建固定相对移动子图。"""
    nodes = MotionWorkflowNodes(gateway_factory)
    builder = StateGraph(CarAgentState)
    builder.add_node("initialize", nodes.initialize)
    builder.add_node("confirm", nodes.confirm)
    builder.add_node("execute_next", nodes.execute_next)
    builder.add_node("finish", nodes.finish)
    builder.add_edge(START, "initialize")
    builder.add_conditional_edges(
        "initialize",
        _after_initialize,
        {"confirm": "confirm", "finish": "finish"},
    )
    builder.add_conditional_edges(
        "confirm",
        _after_confirmation,
        {"execute": "execute_next", "finish": "finish"},
    )
    builder.add_conditional_edges(
        "execute_next",
        _after_execution,
        {"execute": "execute_next", "finish": "finish"},
    )
    builder.add_edge("finish", END)
    return builder.compile(name=name, checkpointer=checkpointer)


def _after_initialize(state: CarAgentState) -> Literal["confirm", "finish"]:
    return (
        "confirm" if state.get("motion_status") == "awaiting_confirmation" else "finish"
    )


def _after_confirmation(state: CarAgentState) -> Literal["execute", "finish"]:
    return "execute" if state.get("motion_status") == "executing" else "finish"


def _after_execution(state: CarAgentState) -> Literal["execute", "finish"]:
    return "execute" if state.get("motion_status") == "executing" else "finish"


def is_confirmed(answer: Any) -> bool:
    """只接受明确确认，含糊回答一律取消。"""
    if isinstance(answer, Mapping):
        confirmed = answer.get("confirmed")
        if isinstance(confirmed, bool):
            return confirmed
        answer = answer.get("answer", "")
    if isinstance(answer, bool):
        return answer
    normalized = "".join(str(answer or "").lower().split()).strip("，。！!")
    return normalized in {"确认", "确认执行", "执行", "yes", "y"}


def format_plan(actions: list[dict[str, Any]]) -> str:
    """生成确定性的用户确认摘要。"""
    parts = []
    for index, action in enumerate(actions, start=1):
        label = ACTION_LABELS.get(str(action.get("type")), str(action.get("type")))
        unit = MODE_UNITS.get(str(action.get("mode")), "")
        parts.append(f"{index}. {label} {action.get('value'):g} {unit}")
    return "；".join(parts)

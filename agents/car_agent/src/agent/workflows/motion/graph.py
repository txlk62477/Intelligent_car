"""人工确认后逐条调用 Robot Gateway 的相对移动子图。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from typing import Any, Literal, TypedDict, cast
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import NotRequired

from agent.common.robot_gateway import (
    RobotGateway,
    RobotGatewayError,
    get_robot_gateway,
)
from agent.state.car_agent import CarAgentState, MotionResult, WorkflowStatus
from agent.tools.requests import MotionAction
from agent.workflows.recovery import (
    UNKNOWN_STATUS,
    best_effort,
    is_pending,
    not_submitted_record,
    recover_submission,
    unknown_record,
)

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


class MotionWorkflowInput(TypedDict):
    """移动子图的外部输入；内部执行状态不得由调用者提供。"""

    motion_actions: list[MotionAction]
    # 由主图按步骤下发：同一个 step_id 恢复时复用同一批 operation_id。
    motion_plan_id: NotRequired[str]


class MotionWorkflowOutput(TypedDict):
    """移动子图完成后向调用者公开的唯一结果。"""

    motion_result: MotionResult


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
                    "小车将按顺序执行以下动作。默认直线速度为 0.27 m/s、"
                    "转向角速度为 0.53 rad/s，接近目标时会自动减速。"
                    "当前不使用雷达避障，请确保周围安全后确认。"
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
        # operation_id 由“计划 ID + 动作下标”组成：Workflow 因 checkpoint 恢复而
        # 重放同一步时，Gateway 可以用它识别同一个请求，避免重复运动。
        index = int(state.get("motion_action_index", 0))
        actions = list(state.get("motion_actions", []))
        action = dict(actions[index])
        operation_id = f"{state['motion_plan_id']}:{index}"
        payload = {"operation_id": operation_id, **action}

        # Gateway 使用同步 HTTP 客户端，因此全部调用都放到工作线程，避免阻塞事件循环。
        gateway = self._gateway_factory()
        try:
            current = await self._submit(gateway, payload, operation_id)
            if is_pending(current, TERMINAL_STATUSES):
                current = await self._await_terminal(
                    gateway, payload, operation_id, current
                )
        except asyncio.CancelledError:
            # Graph 运行被外部取消：动作可能已经下发，先尽力停车再向上抛出取消。
            await best_effort(gateway.stop)
            raise

        results = [*state.get("motion_action_results", []), dict(current)]
        if current.get("status") == "SUCCEEDED":
            next_index = index + 1
            return {
                "motion_action_results": results,
                "motion_action_index": next_index,
                # 还有动作时回到 execute_next；最后一个动作完成后进入 finish。
                "motion_status": "success"
                if next_index >= len(actions)
                else "executing",
                "motion_error": "",
            }

        # 当前动作只要不是 SUCCEEDED，就终止整段计划，不再执行后续动作。
        return {
            "motion_action_results": results,
            "motion_status": _plan_outcome(current),
            "motion_error": str(
                current.get("error") or current.get("status") or "动作失败"
            ),
        }

    async def _submit(
        self, gateway: RobotGateway, payload: dict[str, Any], operation_id: str
    ) -> dict[str, Any]:
        """提交单个动作；提交结果未知时按同一 operation_id 查询真实状态。"""
        try:
            return dict(await asyncio.to_thread(gateway.submit_motion, payload))
        except RobotGatewayError as error:
            recovery = await asyncio.to_thread(
                recover_submission,
                getter=gateway.get_motion,
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
            # 查询同样不可用：无法判断动作是否已经开始，先尽力停车再报告状态未知。
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
    ) -> dict[str, Any]:
        """轮询到终态；等待超时先停车，查询失败按状态未知处理。"""
        deadline = asyncio.get_running_loop().time() + self._action_timeout
        try:
            while is_pending(current, TERMINAL_STATUSES):
                if asyncio.get_running_loop().time() >= deadline:
                    await best_effort(gateway.stop)
                    return {
                        **payload,
                        "status": "TIMED_OUT",
                        "error_code": "WORKFLOW_TIMEOUT",
                        "error": (
                            f"{self._action_timeout:g} 秒内未收到动作终态，已下发停止"
                        ),
                    }
                # 轮询间隔限制 HTTP 查询频率，也把执行权交还给事件循环。
                await asyncio.sleep(self._poll_interval)
                current = dict(
                    await asyncio.to_thread(gateway.get_motion, operation_id)
                )
            return current
        except RobotGatewayError as error:
            # 动作已经提交过：状态无法确认时必须报告 execution_unknown，
            # 不能当作“没有移动”，也不能换新的 operation_id 重提。
            await best_effort(gateway.stop)
            return unknown_record(
                operation_id=operation_id,
                error_code=str(error.code or "UNAVAILABLE"),
                error=f"动作已提交但状态无法确认：{error}",
            )

    def finish(self, state: CarAgentState) -> dict[str, MotionResult]:
        """把内部执行状态压缩成 Supervisor 可使用的结构化结果。"""
        status = str(state.get("motion_status", "failed"))
        results = [dict(item) for item in state.get("motion_action_results", [])]
        failed_action = results[-1] if results else None
        if status == "success":
            summary = f"运动计划执行完成，共完成 {len(results)} 个动作。"
            failed_action = None
        elif status == "cancelled":
            summary = "运动计划已取消，小车没有继续移动。"
            failed_action = None
        elif status == "execution_unknown":
            summary = (
                "运动执行状态未知：动作已提交但无法确认结果。"
                "请先确认小车状态，不要直接重试。"
            )
        else:
            summary = f"运动计划执行失败：{state.get('motion_error') or '未知错误'}"
        return {
            "motion_result": MotionResult(
                status=cast(WorkflowStatus, status),
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
    builder = StateGraph(
        CarAgentState,
        input_schema=MotionWorkflowInput,
        output_schema=MotionWorkflowOutput,
    )
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


def _plan_outcome(record: Mapping[str, Any]) -> str:
    """把 Gateway 终态归一化为整段计划的终态。"""
    status = str(record.get("status") or "")
    if status == "CANCELLED":
        return "cancelled"
    if status == UNKNOWN_STATUS:
        return UNKNOWN_STATUS
    return "failed"


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

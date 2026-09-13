"""跟随子图：检测当前画面、目标解析、候选选择、人工确认与执行轮询。

LangGraph 恢复中断时会从被中断节点开头重放执行，因此有副作用的探测
（HTTP 调 Gateway）与 interrupt() 必须放在不同节点：候选列表保存在
状态里，选择节点只读状态并中断，恢复时不会重新探测。
"""

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
from agent.state.car_agent import CarAgentState, FollowResult, WorkflowStatus
from agent.workflows.motion.graph import is_confirmed
from agent.workflows.recovery import (
    UNKNOWN_STATUS,
    best_effort,
    is_pending,
    not_submitted_record,
    recover_submission,
    unknown_record,
)

# 跟随 Action 的终态；TIMED_OUT+TASK_TIMEOUT 表示任务按时长正常结束。
TERMINAL_FOLLOW_STATUSES = {"FAILED", "TIMED_OUT", "CANCELLED", "SUCCEEDED"}
MAX_RESOLVE_ATTEMPTS = 5


class FollowWorkflowInput(TypedDict):
    """跟随子图的外部输入；内部执行状态不得由调用者提供。"""

    follow_target_label: str
    follow_timeout_seconds: float
    # 由主图按步骤下发：同一个 step_id 恢复时复用同一个 operation_id。
    follow_plan_id: NotRequired[str]


class FollowWorkflowOutput(TypedDict):
    """跟随子图完成后向调用者公开的唯一结果。"""

    follow_result: FollowResult


class FollowWorkflowNodes:
    """把探测、候选选择、确认、等待和错误归一化隐藏在子图 interface 后。"""

    def __init__(self, gateway_factory: Callable[[], RobotGateway]) -> None:
        """保存 Gateway 工厂并从环境变量读取轮询参数。"""
        self._gateway_factory = gateway_factory
        self._poll_interval = float(os.getenv("FOLLOW_POLL_INTERVAL", "0.5"))
        self._timeout_grace = float(os.getenv("FOLLOW_TIMEOUT_GRACE", "30.0"))

    def initialize(self, state: CarAgentState) -> dict[str, Any]:
        """校验 handoff 参数并初始化可恢复状态。"""
        label = str(state.get("follow_target_label") or "").strip().lower()
        try:
            timeout = float(state.get("follow_timeout_seconds") or 60.0)
        except (TypeError, ValueError):
            timeout = 0.0
        if not label or not 0.0 < timeout <= 300.0:
            return {
                "follow_status": "failed",
                "follow_error": f"跟随请求无效：target_label={label!r}, timeout_seconds={timeout:g}",
            }
        return {
            "follow_target_label": label,
            "follow_timeout_seconds": timeout,
            "follow_plan_id": str(state.get("follow_plan_id") or uuid4()),
            "follow_selected_from_list": False,
            "follow_resolve_attempts": 0,
            "follow_candidates": [],
            "follow_error": "",
            "follow_observation": None,
            "follow_status": "resolving",
            "follow_result": None,
        }

    async def probe(self, state: CarAgentState) -> dict[str, Any]:
        """探测当前画面；目标命中直接进入确认，否则保存候选列表。"""
        attempts = int(state.get("follow_resolve_attempts", 0))
        if attempts >= MAX_RESOLVE_ATTEMPTS:
            return {
                "follow_status": "failed",
                "follow_error": "多次检测仍未确定目标，请稍后重新发起跟随",
            }
        try:
            detections = await asyncio.to_thread(self._gateway_factory().get_detections)
        except RobotGatewayError as error:
            return {
                "follow_status": "failed",
                "follow_error": f"读取检测结果失败：{error}",
            }
        status = str(detections.get("status", "TIMEOUT"))
        if status == "NO_FRAME":
            return {
                "follow_status": "failed",
                "follow_error": "相机无画面，请检查相机节点是否在运行",
            }
        if status == "TIMEOUT":
            return {
                "follow_status": "failed",
                "follow_error": "YOLO 未及时产生检测结果，请稍后重试",
            }
        target = str(state.get("follow_target_label") or "").strip().lower()
        candidates = [
            item for item in detections.get("detections", []) if isinstance(item, dict)
        ]
        if status == "DETECTED" and any(
            str(item.get("label", "")) == target for item in candidates
        ):
            return {
                "follow_status": "awaiting_confirmation",
                "follow_error": "",
                "follow_selected_from_list": False,
            }
        return {"follow_candidates": candidates, "follow_status": "selecting"}

    def select(self, state: CarAgentState) -> dict[str, Any]:
        """中断并展示候选列表；选择结果只决定目标，不隐含开始执行。"""
        target = str(state.get("follow_target_label") or "")
        candidates = [dict(item) for item in state.get("follow_candidates", [])]
        hint = str(state.get("follow_error") or "")
        answer = interrupt(_selection_payload(target, candidates, hint))
        decision, index = _parse_selection_answer(answer)
        if decision == "cancel":
            return {"follow_status": "cancelled", "follow_error": "用户取消跟随请求"}
        if decision == "select" and index is not None and 1 <= index <= len(candidates):
            label = str(candidates[index - 1].get("label", "")).strip().lower()
            if not label:
                return {"follow_status": "failed", "follow_error": "候选列表数据无效"}
            # 选中候选只确定目标；执行确认由 confirm 节点单独完成。
            return {
                "follow_target_label": label,
                "follow_selected_from_list": True,
                "follow_status": "awaiting_confirmation",
                "follow_error": "",
            }
        # 重新检测回到 probe；无效输入停留在 select 并带上提示。
        if decision == "retry":
            return {
                "follow_status": "resolving",
                "follow_error": "",
                "follow_resolve_attempts": int(state.get("follow_resolve_attempts", 0))
                + 1,
            }
        return {
            "follow_status": "selecting",
            "follow_error": "输入无效：请回复数字序号、取消或重新检测。",
            "follow_resolve_attempts": int(state.get("follow_resolve_attempts", 0)) + 1,
        }

    def confirm(self, state: CarAgentState) -> dict[str, Any]:
        """展示最终目标并单独请求执行确认；候选被选中不等于开始执行。"""
        label = str(state.get("follow_target_label") or "")
        timeout = float(state.get("follow_timeout_seconds") or 60.0)
        selected = bool(state.get("follow_selected_from_list"))
        prefix = (
            f"已选择目标 {label}（来自候选列表）。"
            if selected
            else f"当前画面已检测到目标 {label}。"
        )
        answer = interrupt(
            {
                "type": "confirm_follow_target",
                "message": (
                    f"{prefix}小车将跟踪该目标，最长运行 {timeout:g} 秒。"
                    "请确认周围安全后回复确认，或回复取消。"
                ),
                "target_label": label,
                "timeout_seconds": timeout,
                "selected_from_list": selected,
                "confirmation_hint": '回复确认执行，或传入 {"confirmed": true}',
            }
        )
        if is_confirmed(answer):
            return {"follow_status": "executing"}
        return {
            "follow_status": "cancelled",
            "follow_error": "用户未确认跟随任务",
        }

    async def execute(self, state: CarAgentState) -> dict[str, Any]:
        """提交跟随任务并等待终态，不在 Graph 内发布速度。"""
        label = str(state.get("follow_target_label") or "").strip().lower()
        timeout = float(state.get("follow_timeout_seconds") or 60.0)
        operation_id = f"follow-{state.get('follow_plan_id') or 'unknown'}"
        payload = {
            "operation_id": operation_id,
            "target_label": label,
            "timeout_seconds": timeout,
        }
        gateway = self._gateway_factory()
        try:
            current = await self._submit(gateway, payload, operation_id)
            if is_pending(current, TERMINAL_FOLLOW_STATUSES):
                current = await self._await_terminal(
                    gateway, payload, operation_id, current, timeout
                )
        except asyncio.CancelledError:
            await best_effort(gateway.cancel_follow, operation_id)
            raise

        outcome, message = _follow_outcome(current)
        observation = {
            key: current.get(key)
            for key in (
                "status",
                "elapsed_seconds",
                "target_visible",
                "confidence",
                "center_error",
                "area_ratio",
                "error_code",
                "error",
            )
        }
        return {
            "follow_status": outcome,
            "follow_error": message,
            "follow_observation": observation,
        }

    async def _submit(
        self, gateway: RobotGateway, payload: dict[str, Any], operation_id: str
    ) -> dict[str, Any]:
        """提交跟随任务；提交结果未知时按同一 operation_id 查询真实状态。"""
        try:
            return dict(await asyncio.to_thread(gateway.submit_follow, payload))
        except RobotGatewayError as error:
            recovery = await asyncio.to_thread(
                recover_submission,
                getter=gateway.get_follow,
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
            await best_effort(gateway.cancel_follow, operation_id)
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
        """轮询到终态；等待超时先取消任务，查询失败按状态未知处理。"""
        deadline = asyncio.get_running_loop().time() + timeout + self._timeout_grace
        try:
            while is_pending(current, TERMINAL_FOLLOW_STATUSES):
                if asyncio.get_running_loop().time() >= deadline:
                    await best_effort(gateway.cancel_follow, operation_id)
                    return {
                        **payload,
                        "status": "TIMED_OUT",
                        "error_code": "WORKFLOW_TIMEOUT",
                        "error": "等待任务终态超时，已取消跟随任务",
                    }
                await asyncio.sleep(self._poll_interval)
                current = dict(
                    await asyncio.to_thread(gateway.get_follow, operation_id)
                )
            return current
        except RobotGatewayError as error:
            await best_effort(gateway.cancel_follow, operation_id)
            return unknown_record(
                operation_id=operation_id,
                error_code=str(error.code or "UNAVAILABLE"),
                error=f"跟随任务已提交但状态无法确认：{error}",
            )

    def finish(self, state: CarAgentState) -> dict[str, FollowResult]:
        """把内部执行状态压缩成 Supervisor 可使用的结构化结果。"""
        status = str(state.get("follow_status", "failed"))
        label = str(state.get("follow_target_label") or "")
        observation = state.get("follow_observation")
        if status == "success":
            summary = f"跟随任务已跟踪目标 {label} 至时限并正常结束。"
        elif status == "cancelled":
            summary = f"跟随任务已取消：{state.get('follow_error') or '用户取消'}"
        elif status == "execution_unknown":
            summary = (
                "跟随执行状态未知：任务已提交但无法确认结果。"
                "请先确认小车状态，不要直接重试。"
            )
        else:
            summary = f"跟随任务失败：{state.get('follow_error') or '未知错误'}"
        return {
            "follow_result": FollowResult(
                status=cast(WorkflowStatus, status),
                summary=summary,
                target_label=label,
                final_observation=dict(observation) if observation else None,
            )
        }


def build_follow_workflow(
    *,
    gateway_factory: Callable[[], RobotGateway] = get_robot_gateway,
    name: str = "follow_workflow",
    checkpointer: BaseCheckpointSaver | None = None,
):
    """构建按需检测 + 候选选择 + 人工确认的跟随子图。"""
    nodes = FollowWorkflowNodes(gateway_factory)
    builder = StateGraph(
        CarAgentState,
        input_schema=FollowWorkflowInput,
        output_schema=FollowWorkflowOutput,
    )
    builder.add_node("initialize", nodes.initialize)
    builder.add_node("probe", nodes.probe)
    builder.add_node("select", nodes.select)
    builder.add_node("confirm", nodes.confirm)
    builder.add_node("execute", nodes.execute)
    builder.add_node("finish", nodes.finish)
    builder.add_edge(START, "initialize")
    builder.add_conditional_edges(
        "initialize",
        _after_initialize,
        {"probe": "probe", "finish": "finish"},
    )
    builder.add_conditional_edges(
        "probe",
        _after_probe,
        {"select": "select", "confirm": "confirm", "finish": "finish"},
    )
    builder.add_conditional_edges(
        "select",
        _after_select,
        {
            "probe": "probe",
            "select": "select",
            "confirm": "confirm",
            "finish": "finish",
        },
    )
    builder.add_conditional_edges(
        "confirm",
        _after_confirmation,
        {"execute": "execute", "finish": "finish"},
    )
    builder.add_edge("execute", "finish")
    builder.add_edge("finish", END)
    return builder.compile(name=name, checkpointer=checkpointer)


def _after_initialize(state: CarAgentState) -> Literal["probe", "finish"]:
    return "probe" if state.get("follow_status") == "resolving" else "finish"


def _after_probe(state: CarAgentState) -> Literal["select", "confirm", "finish"]:
    status = str(state.get("follow_status"))
    if status == "selecting":
        return "select"
    if status == "awaiting_confirmation":
        return "confirm"
    return "finish"


def _after_select(
    state: CarAgentState,
) -> Literal["probe", "select", "confirm", "finish"]:
    status = str(state.get("follow_status"))
    if status == "resolving":
        return "probe"
    if status == "selecting":
        return "select"
    if status == "awaiting_confirmation":
        return "confirm"
    return "finish"


def _after_confirmation(state: CarAgentState) -> Literal["execute", "finish"]:
    return "execute" if state.get("follow_status") == "executing" else "finish"


def _follow_outcome(record: Mapping[str, Any]) -> tuple[str, str]:
    """把 Gateway 记录归一化为跟随任务终态与说明。"""
    status = str(record.get("status"))
    error_code = str(record.get("error_code") or "")
    # 按用户给定时间正常结束的跟随不是失败。
    if status == "TIMED_OUT" and error_code == "TASK_TIMEOUT":
        return "success", ""
    if status == "SUCCEEDED":
        return "success", ""
    if status == "CANCELLED":
        return "cancelled", str(record.get("error") or "跟随任务被取消")
    if status == UNKNOWN_STATUS:
        return UNKNOWN_STATUS, str(record.get("error") or "跟随任务状态未知")
    return "failed", str(record.get("error") or error_code or "跟随任务失败")


def _selection_payload(
    target: str, candidates: list[dict[str, Any]], hint: str
) -> dict[str, Any]:
    """生成确定性的候选列表中断载荷。"""
    if candidates:
        lines = []
        for index, item in enumerate(candidates, start=1):
            score = item.get("score")
            position = item.get("position")
            lines.append(
                f"{index}. {item.get('label')} {score}（{position or '位置未知'}）"
            )
        message = f"当前画面未检测到“{target}”。检测到以下物体：\n" + "\n".join(lines)
    else:
        message = "当前画面未检测到任何物体。"
    return {
        "type": "select_follow_target",
        "message": message,
        "target_label": target,
        "candidates": [
            {
                "index": index,
                "label": item.get("label"),
                "score": item.get("score"),
                "position": item.get("position"),
            }
            for index, item in enumerate(candidates, start=1)
        ],
        "empty": not candidates,
        "hint": hint
        or "回复数字序号选择要跟随的目标；回复“取消”停止；回复“重新检测”再看一遍。",
    }


def _parse_selection_answer(answer: Any) -> tuple[str, int | None]:
    """解析候选选择回复：select/cancel/retry/invalid + 目标序号。"""
    if isinstance(answer, Mapping):
        answer = answer.get("answer") or answer.get("selection")
    if isinstance(answer, bool):
        return ("invalid", None)
    normalized = "".join(str(answer or "").lower().split()).strip("，。！!,. ")
    if normalized in {"取消", "算了", "不跟了", "cancel", "stop"}:
        return ("cancel", None)
    if normalized in {"重新检测", "重试", "再看看", "retry", "redetect"}:
        return ("retry", None)
    if normalized.isdigit():
        return ("select", int(normalized))
    return ("invalid", None)


__all__ = [
    "MAX_RESOLVE_ATTEMPTS",
    "TERMINAL_FOLLOW_STATUSES",
    "build_follow_workflow",
]

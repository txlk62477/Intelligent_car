"""Supervisor 主图与相对移动子图共享的状态。"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import MessagesState
from typing_extensions import NotRequired


class MotionResult(TypedDict):
    """移动子图返回 Supervisor 的唯一结构化结果。"""

    # 整段运动计划的最终结果。
    status: str  # success、failed 或 cancelled
    summary: str  # 供 Supervisor 生成回复的中文摘要

    # 每个原子动作的执行明细。
    completed_actions: list[dict[str, Any]]  # 所有状态为 SUCCEEDED 的动作
    failed_action: dict[str, Any] | None  # 失败动作；没有失败时为 None


class CarAgentInput(MessagesState):
    """主图的外部输入；调用者只需提供对话消息。"""


class CarAgentOutput(MessagesState):
    """主图的外部输出；最后一条消息是 Supervisor 的最终回复。"""


class CarAgentState(MessagesState):
    """主图状态；运行时 ROS2 对象永不进入 checkpoint。"""

    # 对话上下文由 MessagesState 提供 messages 字段，保存用户、AI 和 Tool 消息。

    # Supervisor → 移动子图：结构化 handoff 数据。
    motion_actions: NotRequired[list[dict[str, Any]]]  # 按用户顺序排列的动作列表
    motion_tool_call_id: NotRequired[str]  # 对应 AIMessage 的工具调用 ID

    # 移动子图内部：支持串行执行、人工确认中断及 checkpoint 恢复。
    motion_plan_id: NotRequired[str]  # 计划 ID，也是 operation_id 前缀
    motion_action_index: NotRequired[int]  # 当前正在执行的动作下标
    motion_action_results: NotRequired[list[dict[str, Any]]]  # 已执行动作结果
    motion_status: NotRequired[str]  # 子图当前阶段或最终状态
    motion_error: NotRequired[str]  # 子图失败或取消原因

    # 移动子图 → Supervisor：压缩后的唯一结构化输出。
    motion_result: NotRequired[MotionResult | None]  # collect 节点将其转成 ToolMessage

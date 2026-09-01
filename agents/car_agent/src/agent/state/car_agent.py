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


class FollowResult(TypedDict):
    """跟随子图返回 Supervisor 的唯一结构化结果。"""

    status: str  # success、failed 或 cancelled
    summary: str  # 供 Supervisor 生成回复的中文摘要
    target_label: str  # 最终实际跟随的目标类别
    final_observation: dict[str, Any] | None  # 任务终态时的控制观测


class LocationResult(TypedDict):
    """地图位置新增、更新或删除的结构化结果。"""

    status: str
    summary: str
    action: str
    location: dict[str, Any] | None


class NavigationResult(TypedDict):
    """Nav2 地点导航的结构化结果。"""

    status: str
    summary: str
    location: dict[str, Any] | None
    final_observation: dict[str, Any] | None


class CarAgentInput(MessagesState):
    """主图的外部输入；调用者只需提供对话消息。"""


class CarAgentOutput(MessagesState):
    """主图的外部输出；最后一条消息是 Supervisor 的最终回复。"""


class CarAgentState(MessagesState):
    """主图状态；运行时 ROS2 对象永不进入 checkpoint。"""

    # 对话上下文由 MessagesState 提供 messages 字段，保存用户、AI 和 Tool 消息。

    # Agent Server Store 长期记忆；公共输出 Schema 不暴露这些内部字段。
    memory_user_id: NotRequired[str]
    memory_robot_id: NotRequired[str]
    memory_turn_start_message_id: NotRequired[str | None]
    memory_profile: NotRequired[dict[str, Any]]
    memory_episodes: NotRequired[list[dict[str, Any]]]
    memory_context: NotRequired[str]
    memory_load_error: NotRequired[str]
    memory_saved: NotRequired[bool]
    memory_save_error: NotRequired[str]
    memory_extraction_error: NotRequired[str]

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

    # Supervisor handoff 簿记：标记刚运行的是哪个子图，供合并后的 collect 节点配对。
    pending_handoff_kind: NotRequired[str]  # "motion" 或 "follow"

    # Supervisor → 跟随子图：结构化 handoff 数据。
    follow_target_label: NotRequired[str]  # 单个 YOLO COCO 英文类别名
    follow_timeout_seconds: NotRequired[float]  # 跟随总时限，默认 60 秒
    follow_tool_call_id: NotRequired[str]  # 对应 AIMessage 的工具调用 ID

    # 跟随子图内部：目标解析、人工确认中断、串行等待与 checkpoint 恢复。
    follow_plan_id: NotRequired[str]  # 计划 ID，也是 operation_id 前缀
    follow_status: NotRequired[str]  # 子图当前阶段或最终状态
    follow_error: NotRequired[str]  # 子图失败或取消原因
    follow_selected_from_list: NotRequired[bool]  # 目标是否来自候选列表选择
    follow_resolve_attempts: NotRequired[int]  # resolve 中断重试次数
    follow_candidates: NotRequired[list[dict[str, Any]]]  # 展示给用户的候选列表
    follow_observation: NotRequired[dict[str, Any] | None]  # 任务终态观测

    # 跟随子图 → Supervisor：压缩后的唯一结构化输出。
    follow_result: NotRequired[FollowResult | None]  # collect 节点将其转成 ToolMessage

    # Supervisor → 地图位置教学/删除 Workflow。
    location_action: NotRequired[str]
    location_query: NotRequired[str]
    location_label: NotRequired[str]
    location_aliases: NotRequired[list[str]]
    location_tool_call_id: NotRequired[str]
    location_plan_id: NotRequired[str]
    location_status: NotRequired[str]
    location_error: NotRequired[str]
    location_map_status: NotRequired[dict[str, Any]]
    location_existing: NotRequired[dict[str, Any] | None]
    location_selected: NotRequired[dict[str, Any] | None]
    location_candidates: NotRequired[list[dict[str, Any]]]
    location_result: NotRequired[LocationResult | None]

    # Supervisor → Nav2 地点导航 Workflow。
    navigation_timeout_seconds: NotRequired[float]
    navigation_tool_call_id: NotRequired[str]
    navigation_plan_id: NotRequired[str]
    navigation_status: NotRequired[str]
    navigation_error: NotRequired[str]
    navigation_operation: NotRequired[dict[str, Any] | None]
    navigation_result: NotRequired[NavigationResult | None]

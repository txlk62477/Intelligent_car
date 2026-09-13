"""Agent 只能提交请求的编排工具 schema。

工具只声明"要做什么"，不执行任何机器人动作：主图截获调用、按 kind 用严格模型
重新校验参数，再决定是否交给固定 Workflow。因此这里的字段说明必须写清每种
kind 的确切形状，让模型能一次构造出合法请求。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from langchain_core.tools import tool

WorkflowKind = Literal[
    "motion",
    "follow",
    "save_location",
    "delete_location",
    "navigation",
]

_ARGUMENT_SHAPES = (
    "arguments 是对象，字段必须严格按 kind 提供："
    'motion → {"actions": [{"type": "forward|backward|turn_left|turn_right", '
    '"mode": "distance|angle|time", "value": 数值}]}；'
    'follow → {"target_label": "英文类别", "timeout_seconds": 60}；'
    'save_location → {"label": "地点名称", "aliases": ["别名"]}；'
    'delete_location → {"location": "地点名称"}；'
    'navigation → {"location": "地点名称", "timeout_seconds": 300}。'
    "禁止添加任务 ID、确认状态或 operation_id 等内部字段。"
)


@tool
def request_workflow(
    kind: Annotated[WorkflowKind, "要请求的固定 Workflow 类型"],
    arguments: Annotated[dict[str, Any], _ARGUMENT_SHAPES],
    source_observation_ids: Annotated[
        list[str] | None,
        "可选：支持此次判断的当前任务观察 ID；引用不等于执行授权",
    ] = None,
    step_description: Annotated[str, "当前步骤的简短中文标题，用于任务列表展示"] = "",
    remaining_goals_after_success: Annotated[
        list[str] | None,
        "此步成功后的待完成步骤标题；最后一步传 []；只有执行成功才更新任务进度",
    ] = None,
) -> str:
    """请求受代码校验、人工确认和预算约束的固定 Workflow。"""
    return "该请求由主图编排入口处理，不应在 Agent 内直接执行。"


@tool
def ask_user(question: Annotated[str, "只包含一个必要的澄清问题"]):
    """请求用户补充执行所需的信息，并保持当前任务。"""
    return question


__all__ = ["WorkflowKind", "ask_user", "request_workflow"]

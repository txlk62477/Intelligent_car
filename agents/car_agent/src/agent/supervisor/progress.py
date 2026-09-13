"""把代码维护的任务记录投影为可展示的总目标和步骤列表。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def with_progress(state: Mapping[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """同步展示字段；展示文本不作为执行、确认或完成的证据。"""
    merged = {**state, **update}
    steps = [
        {
            "title": str(step.get("description") or step.get("kind") or "任务步骤"),
            "status": "completed",
        }
        for step in merged.get("completed_steps", [])
    ]
    current = merged.get("current_step")
    remaining = list(merged.get("remaining_goals", []))
    if isinstance(current, dict):
        steps.append(
            {
                "title": str(
                    current.get("description") or current.get("kind") or "任务步骤"
                ),
                "status": str(current.get("status") or "running"),
            }
        )
        # 当前步骤还未成功，剩余目标的权威记录不改；展示时使用绑定的待办建议。
        proposed = current.get("remaining_goals_after_success")
        if isinstance(proposed, list):
            remaining = proposed
    steps.extend({"title": title, "status": "pending"} for title in remaining)
    update["task_progress"] = {
        "goal": str(merged.get("goal") or ""),
        "status": str(merged.get("task_status") or "active"),
        "steps": steps,
        "completed_count": len(merged.get("completed_steps", [])),
        "stop_reason": str(merged.get("stop_reason") or ""),
    }
    return update

"""薄路由与灵活 Agent 的工具集合。"""

from agent.tools.navigation import (
    delegate_to_delete_location_workflow,
    delegate_to_navigation_workflow,
    delegate_to_save_location_workflow,
)
from agent.tools.perception import delegate_to_follow_workflow
from agent.tools.robot import delegate_to_motion_workflow, get_robot_status, stop_robot
from agent.tools.vision import recognize_image

# 固定高成本子图的委派工具。
DELEGATION_TOOLS = [
    delegate_to_motion_workflow,
    delegate_to_follow_workflow,
    delegate_to_save_location_workflow,
    delegate_to_navigation_workflow,
    delegate_to_delete_location_workflow,
]

# 薄路由可调用的工具：急停 + 子图委派。
ROUTER_TOOLS = [stop_robot, *DELEGATION_TOOLS]

# 灵活 Agent（create_agent + SummarizationMiddleware）可调用的轻量工具。
FLEXIBLE_TOOLS = [get_robot_status, recognize_image]

__all__ = [
    "DELEGATION_TOOLS",
    "FLEXIBLE_TOOLS",
    "ROUTER_TOOLS",
    "delegate_to_delete_location_workflow",
    "delegate_to_follow_workflow",
    "delegate_to_motion_workflow",
    "delegate_to_navigation_workflow",
    "delegate_to_save_location_workflow",
    "get_robot_status",
    "recognize_image",
    "stop_robot",
]

"""Supervisor 可见工具及其注册集合。"""

from agent.tools.navigation import (
    delegate_to_delete_location_workflow,
    delegate_to_navigation_workflow,
    delegate_to_save_location_workflow,
)
from agent.tools.perception import delegate_to_follow_workflow
from agent.tools.robot import DIRECT_TOOLS as ROBOT_DIRECT_TOOLS
from agent.tools.robot import delegate_to_motion_workflow
from agent.tools.vision import recognize_image

DIRECT_TOOLS = [*ROBOT_DIRECT_TOOLS, recognize_image]
SUPERVISOR_TOOLS = [
    *DIRECT_TOOLS,
    delegate_to_motion_workflow,
    delegate_to_follow_workflow,
    delegate_to_save_location_workflow,
    delegate_to_navigation_workflow,
    delegate_to_delete_location_workflow,
]

__all__ = [
    "DIRECT_TOOLS",
    "SUPERVISOR_TOOLS",
    "delegate_to_follow_workflow",
    "delegate_to_save_location_workflow",
    "delegate_to_navigation_workflow",
    "delegate_to_delete_location_workflow",
    "delegate_to_motion_workflow",
    "recognize_image",
]

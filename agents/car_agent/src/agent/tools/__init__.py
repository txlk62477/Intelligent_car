"""Supervisor 可见工具及其注册集合。"""

from agent.tools.perception import FOLLOW_TOOLS
from agent.tools.robot import DIRECT_TOOLS as ROBOT_DIRECT_TOOLS
from agent.tools.robot import delegate_to_motion_workflow
from agent.tools.vision import recognize_image

DIRECT_TOOLS = [*ROBOT_DIRECT_TOOLS, recognize_image, *FOLLOW_TOOLS]
SUPERVISOR_TOOLS = [*DIRECT_TOOLS, delegate_to_motion_workflow]

__all__ = [
    "DIRECT_TOOLS",
    "SUPERVISOR_TOOLS",
    "delegate_to_motion_workflow",
    "FOLLOW_TOOLS",
    "recognize_image",
]

"""Agent 可见工具集合。

Agent 只有三类能力：只读观察、受限控制请求（编排/澄清/停车）和图片识别。
任何有副作用的机器人动作都不在这里执行，而是由主图编排入口校验后交给固定
Workflow。
"""

from agent.tools.orchestration import ask_user, request_workflow
from agent.tools.robot import get_robot_status, stop_robot
from agent.tools.vision import recognize_image

#: Agent 在非终态可调用的全部工具；控制类调用会被中间件截获。
AGENT_TOOLS = [
    get_robot_status,
    recognize_image,
    request_workflow,
    ask_user,
    stop_robot,
]

#: 只读工具；其结果会生成 observation_id 供 Workflow 请求引用。
READ_ONLY_TOOLS = {get_robot_status.name, recognize_image.name}

__all__ = [
    "AGENT_TOOLS",
    "READ_ONLY_TOOLS",
    "ask_user",
    "get_robot_status",
    "recognize_image",
    "request_workflow",
    "stop_robot",
]

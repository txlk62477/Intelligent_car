"""相对移动 Workflow 的独立 LangGraph 调试入口。"""

from agent.workflows.motion import build_motion_workflow

# 将原本嵌入 Supervisor 的移动子图单独编译并导出，使 LangGraph Studio
# 可以直接选择和调试它。执行时仍会先请求人工确认，确认后调用真实 Gateway。
graph = build_motion_workflow()

__all__ = ["graph"]

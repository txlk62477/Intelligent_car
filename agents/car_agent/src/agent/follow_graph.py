"""跟随 Workflow 的独立 LangGraph 调试入口。"""

from agent.workflows.follow import build_follow_workflow

# 将原本嵌入 Supervisor 的跟随子图单独编译并导出，使 LangGraph Studio
# 可以直接选择和调试它。执行时仍会先探测当前画面并请求人工确认，确认后
# 调用真实 Gateway 提交跟随任务。
graph = build_follow_workflow()

__all__ = ["graph"]

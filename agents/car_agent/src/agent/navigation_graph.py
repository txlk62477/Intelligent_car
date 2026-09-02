"""已命名地点 Nav2 导航 Workflow 的独立 LangGraph 调试入口。"""

from agent.workflows.navigation import build_navigation_workflow

# Agent Server 会为该独立图注入官方 Store 和 checkpointer。执行时
# 仍会从当前地图的位置资产中解析目标，完成路径预检并在导航前中断确认。
graph = build_navigation_workflow()

__all__ = ["graph"]

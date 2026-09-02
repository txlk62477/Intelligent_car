"""地图位置教学/删除 Workflow 的独立 LangGraph 调试入口。"""

from agent.workflows.location import build_location_workflow

# Agent Server 会为该独立图注入官方 Store 和 checkpointer。执行时
# 仍会读取真实 Gateway 的当前地图/AMCL 位姿，并在写入或删除前中断确认。
graph = build_location_workflow()

__all__ = ["graph"]

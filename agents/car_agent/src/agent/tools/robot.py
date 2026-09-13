"""只读状态与急停工具。"""

from __future__ import annotations

from langchain_core.tools import tool

from agent.common.robot_gateway import get_robot_gateway


@tool
def get_robot_status() -> dict:
    """查询小车在线状态、EKF 融合相对位姿、速度以及当前或最近运动结果。"""
    return get_robot_gateway().get_status()


@tool
def stop_robot() -> dict:
    """立即停止小车并取消当前运动。此工具无需人工确认。"""
    return get_robot_gateway().stop()


__all__ = ["get_robot_status", "stop_robot"]

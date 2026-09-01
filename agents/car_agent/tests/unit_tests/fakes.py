"""测试专用 Fake：内存版 Robot Gateway 与可编程 Chat Model。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from agent.common.robot_gateway import RobotGatewayError


class FakeRobotGateway:
    """记录调用、可编程响应序列的内存 Gateway。"""

    def __init__(
        self,
        submit_results: list[dict[str, Any]] | None = None,
        poll_scripts: dict[str, list[dict[str, Any]]] | None = None,
        *,
        snapshot_script: list[dict[str, Any]] | None = None,
        detections_script: list[dict[str, Any]] | None = None,
        navigation_status: dict[str, Any] | None = None,
    ) -> None:
        self.submitted: list[dict[str, Any]] = []
        self.stop_calls = 0
        self.status_calls = 0
        self.snapshot_calls = 0
        self._submit_results = list(submit_results or [])
        self._poll_scripts = {
            key: list(value) for key, value in (poll_scripts or {}).items()
        }
        self._records: dict[str, dict[str, Any]] = {}
        self.follow_submitted: list[dict[str, Any]] = []
        self._follow_records: dict[str, dict[str, Any]] = {}
        self._snapshot_script = list(snapshot_script or [])
        self._detections_script = list(detections_script or [])
        self.detections_calls = 0
        self.navigation_status = navigation_status or {
            "status": "READY",
            "map_id": "sha256:test-map",
            "map_name": "test-map",
            "frame_id": "map",
            "pose": {"x": 1.0, "y": 2.0, "yaw": 0.5, "frame_id": "map"},
        }
        self.navigation_submitted: list[dict[str, Any]] = []
        self._navigation_records: dict[str, dict[str, Any]] = {}

    def get_status(self) -> dict[str, Any]:
        self.status_calls += 1
        return {
            "online": True,
            "gateway_status": "IDLE",
            "pose": None,
            "velocity": None,
        }

    def submit_motion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """幂等提交：同一 operation_id 直接返回已有记录。"""

        operation_id = str(payload["operation_id"])
        previous = self._records.get(operation_id)
        if previous is not None:
            return previous
        self.submitted.append(payload)
        if self._submit_results:
            record = {**payload, **dict(self._submit_results.pop(0))}
        else:
            record = {**payload, "status": "RUNNING"}
        self._records[operation_id] = record
        return record

    def get_motion(self, operation_id: str) -> dict[str, Any]:
        """按脚本逐次推进状态，脚本耗尽后保持最后状态。"""

        record = self._records.get(operation_id)
        if record is None:
            raise RobotGatewayError("NOT_FOUND", "未知 operation_id")
        script = self._poll_scripts.get(operation_id)
        if script:
            record.update(dict(script.pop(0)))
        return record

    def stop(self) -> dict[str, Any]:
        self.stop_calls += 1
        for record in self._records.values():
            if record.get("status") == "RUNNING":
                record.update(
                    {
                        "status": "CANCELLED",
                        "error_code": "CANCELLED",
                        "error": "收到停止请求",
                    }
                )
        for record in self._follow_records.values():
            if record.get("status") not in {
                "SUCCEEDED",
                "FAILED",
                "TIMED_OUT",
                "CANCELLED",
            }:
                record.update(
                    {
                        "status": "CANCELLED",
                        "error_code": "CANCELLED",
                        "error": "收到停止请求",
                    }
                )
        for record in self._navigation_records.values():
            if record.get("status") not in {
                "SUCCEEDED",
                "FAILED",
                "TIMED_OUT",
                "CANCELLED",
            }:
                record.update(
                    {
                        "status": "CANCELLED",
                        "error_code": "CANCELLED",
                        "error": "收到停止请求",
                    }
                )
        return {"gateway_status": "IDLE"}

    def submit_follow(self, payload: dict[str, Any]) -> dict[str, Any]:
        """创建内存视觉跟随记录；可用 submit_results 脚本推入终态。"""
        operation_id = str(payload["operation_id"])
        previous = self._follow_records.get(operation_id)
        if previous is not None:
            return previous
        self.follow_submitted.append(payload)
        record = {
            **payload,
            "kind": "follow",
            "status": "STARTING",
            "target_visible": False,
        }
        if self._submit_results:
            record.update(dict(self._submit_results.pop(0)))
        self._follow_records[operation_id] = record
        return record

    def get_follow(self, operation_id: str) -> dict[str, Any]:
        """查询内存视觉跟随记录；可用 poll_scripts 脚本逐次推进。"""
        try:
            record = self._follow_records[operation_id]
        except KeyError as error:
            raise RobotGatewayError("NOT_FOUND", "未知 operation_id") from error
        script = self._poll_scripts.get(operation_id)
        if script:
            record.update(dict(script.pop(0)))
        return record

    def cancel_follow(self, operation_id: str) -> dict[str, Any]:
        """取消内存视觉跟随记录。"""
        record = self.get_follow(operation_id)
        record.update({"status": "CANCELLED", "error_code": "CANCELLED"})
        return record

    def get_camera_snapshot(self) -> dict[str, Any]:
        """按脚本逐次返回相机快照结果，脚本耗尽后返回固定路径。"""
        self.snapshot_calls = getattr(self, "snapshot_calls", 0) + 1
        if self._snapshot_script:
            return dict(self._snapshot_script.pop(0))
        return {
            "status": "captured",
            "path": "/tmp/snapshot_frame.jpg",
            "format": "jpeg",
        }

    def get_detections(self) -> dict[str, Any]:
        """按脚本逐次返回检测快照，脚本耗尽后返回空检测。"""
        self.detections_calls += 1
        if self._detections_script:
            return dict(self._detections_script.pop(0))
        return {"status": "EMPTY", "detections": []}

    def get_navigation_status(self) -> dict[str, Any]:
        return dict(self.navigation_status)

    def preflight_navigation(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("map_id") != self.navigation_status.get("map_id"):
            raise RobotGatewayError("MAP_CHANGED", "目标位置不属于当前地图")
        return {**payload, "status": "READY", "path_pose_count": 10}

    def submit_navigation(self, payload: dict[str, Any]) -> dict[str, Any]:
        operation_id = str(payload["operation_id"])
        previous = self._navigation_records.get(operation_id)
        if previous is not None:
            return previous
        self.navigation_submitted.append(payload)
        record = {**payload, "kind": "navigation", "status": "RUNNING"}
        if self._submit_results:
            record.update(dict(self._submit_results.pop(0)))
        self._navigation_records[operation_id] = record
        return record

    def get_navigation(self, operation_id: str) -> dict[str, Any]:
        try:
            record = self._navigation_records[operation_id]
        except KeyError as error:
            raise RobotGatewayError("NOT_FOUND", "未知 operation_id") from error
        script = self._poll_scripts.get(operation_id)
        if script:
            record.update(dict(script.pop(0)))
        elif record["status"] == "RUNNING":
            record["status"] = "SUCCEEDED"
        return record

    def cancel_navigation(self, operation_id: str) -> dict[str, Any]:
        record = self.get_navigation(operation_id)
        record.update({"status": "CANCELLED", "error_code": "CANCELLED"})
        return record


class FailingRobotGateway(FakeRobotGateway):
    """提交成功但后续查询持续失败的 Gateway。"""

    def get_motion(self, operation_id: str) -> dict[str, Any]:
        raise RobotGatewayError("UNAVAILABLE", "Robot Gateway 不可用")

    def get_follow(self, operation_id: str) -> dict[str, Any]:
        raise RobotGatewayError("UNAVAILABLE", "Robot Gateway 不可用")


class FakeChatModel:
    """按队列返回预置消息的可编程 Chat Model。"""

    def __init__(self, responses: list[BaseMessage] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[list[BaseMessage]] = []
        self.bound_tools: list[Any] = []

    def bind_tools(
        self,
        tools: list[Any],
        *,
        parallel_tool_calls: bool = False,
    ) -> FakeChatModel:
        self.bound_tools = list(tools)
        return self

    async def ainvoke(self, messages: list[BaseMessage]) -> BaseMessage:
        self.calls.append(list(messages))
        if not self._responses:
            return AIMessage(content="（没有更多预设回复）")
        return self._responses.pop(0)


def tool_call_ai(name: str, args: dict[str, Any], call_id: str = "call-1") -> AIMessage:
    """构造携带单个 tool_call 的 AIMessage。"""

    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )

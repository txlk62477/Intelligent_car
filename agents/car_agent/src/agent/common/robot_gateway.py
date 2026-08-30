"""LangGraph 侧的 Robot Gateway interface 与 HTTP Adapter。"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class RobotGatewayError(RuntimeError):
    """把 HTTP、网络和 Gateway 领域错误归一化。"""

    def __init__(self, code: str, message: str) -> None:
        """构造带稳定错误码的归一化异常。"""
        super().__init__(message)
        self.code = code


class RobotGateway(Protocol):
    """移动 Workflow 与状态 Tool 共用的小接口。"""

    def get_status(self) -> dict[str, Any]:
        """读取机器人与 Gateway 状态快照。"""
        ...

    def submit_motion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """幂等提交一个原子动作。"""
        ...

    def get_motion(self, operation_id: str) -> dict[str, Any]:
        """查询原子动作的执行状态。"""
        ...

    def stop(self) -> dict[str, Any]:
        """无条件请求立即停车。"""
        ...

    def submit_follow(self, payload: dict[str, Any]) -> dict[str, Any]:
        """提交按类别跟随目标的任务。"""
        ...

    def get_follow(self, operation_id: str) -> dict[str, Any]:
        """查询视觉跟随任务状态。"""
        ...

    def cancel_follow(self, operation_id: str) -> dict[str, Any]:
        """取消指定视觉跟随任务。"""
        ...

    def get_camera_snapshot(self) -> dict[str, Any]:
        """抓取小车相机最新帧并保存为本地文件。"""
        ...

    def get_detections(self) -> dict[str, Any]:
        """读取当前画面的 YOLO 检测快照。"""
        ...


class HttpRobotGateway:
    """通过本机 JSON/HTTP 调用 ROS2 Gateway。"""

    def __init__(self, base_url: str, *, timeout: float = 2.0) -> None:
        """构造指向固定 base_url 的 HTTP Adapter。"""
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def get_status(self) -> dict[str, Any]:
        """读取机器人与 Gateway 状态快照。"""
        return self._request("GET", "/v1/robot/status")

    def submit_motion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """幂等提交一个原子动作。"""
        return self._request("POST", "/v1/motions", payload)

    def get_motion(self, operation_id: str) -> dict[str, Any]:
        """查询原子动作的执行状态。"""
        return self._request("GET", f"/v1/motions/{quote(operation_id, safe='')}")

    def stop(self) -> dict[str, Any]:
        """无条件请求立即停车。"""
        return self._request("POST", "/v1/stop", {})

    def submit_follow(self, payload: dict[str, Any]) -> dict[str, Any]:
        """创建视觉跟随任务。"""
        return self._request("POST", "/v1/follow-tasks", payload)

    def get_follow(self, operation_id: str) -> dict[str, Any]:
        """查询视觉跟随任务。"""
        return self._request("GET", f"/v1/follow-tasks/{quote(operation_id, safe='')}")

    def cancel_follow(self, operation_id: str) -> dict[str, Any]:
        """取消视觉跟随任务。"""
        return self._request(
            "POST", f"/v1/follow-tasks/{quote(operation_id, safe='')}/cancel", {}
        )

    def get_camera_snapshot(self) -> dict[str, Any]:
        """抓取小车相机最新帧并保存为本地 jpeg 文件。"""
        return self._request("GET", "/v1/camera/snapshot")

    def get_detections(self) -> dict[str, Any]:
        """读取当前画面的 YOLO 检测快照。"""
        return self._request("GET", "/v1/perception/detections")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self._base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                result = json.load(response)
        except HTTPError as error:
            try:
                result = json.load(error)
            except (json.JSONDecodeError, UnicodeDecodeError):
                result = {}
            raise RobotGatewayError(
                str(result.get("error_code") or f"HTTP_{error.code}"),
                str(result.get("error") or error.reason),
            ) from error
        except (TimeoutError, URLError, OSError) as error:
            raise RobotGatewayError(
                "UNAVAILABLE", f"Robot Gateway 不可用：{error}"
            ) from error
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            raise RobotGatewayError(
                "INVALID_RESPONSE", "Robot Gateway 返回了无效 JSON"
            ) from error
        if not isinstance(result, dict):
            raise RobotGatewayError("INVALID_RESPONSE", "Robot Gateway 返回值不是对象")
        return result


@lru_cache(maxsize=1)
def get_robot_gateway() -> HttpRobotGateway:
    """从环境变量创建默认 HTTP Adapter。"""
    return HttpRobotGateway(
        os.getenv("ROBOT_GATEWAY_URL", "http://127.0.0.1:8765"),
        timeout=float(os.getenv("ROBOT_GATEWAY_HTTP_TIMEOUT", "2.0")),
    )

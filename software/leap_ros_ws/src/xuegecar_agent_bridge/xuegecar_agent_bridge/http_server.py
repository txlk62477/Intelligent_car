"""Robot Gateway 的小型 JSON/HTTP seam。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from urllib.parse import unquote

from xuegecar_agent_bridge.errors import GatewayRejected

MAX_BODY_BYTES = 64 * 1024
# operation_id 只能占据最后一个路径段，避免把额外的子路径误当成动作 ID。
OPERATION_PATH = re.compile(r"^/v1/motions/([^/]+)$")
FOLLOW_PATH = re.compile(r"^/v1/follow-tasks/([^/]+)$")
CANCEL_FOLLOW_PATH = re.compile(r"^/v1/follow-tasks/([^/]+)/cancel$")


def _rejection_status(code: str) -> int:
    """把 Gateway 领域错误码映射为 HTTP 状态码。"""
    if code in {"BUSY", "ID_CONFLICT"}:
        return 409
    if code == "NOT_FOUND":
        return 404
    if code in {"UNAVAILABLE", "GATEWAY_TIMEOUT", "NO_FRAME", "STALE_FRAME"}:
        return 503
    if code == "SNAPSHOT_WRITE_ERROR":
        return 500
    return 422


class GatewayHttpServer:
    """在后台线程运行的依赖最小 HTTP Adapter。"""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        status: Callable[[], dict[str, Any]],
        operation: Callable[[str], dict[str, Any] | None],
        submit: Callable[[dict[str, Any]], dict[str, Any]],
        follow_operation: Callable[[str], dict[str, Any] | None],
        submit_follow: Callable[[dict[str, Any]], dict[str, Any]],
        cancel_follow: Callable[[str], dict[str, Any]],
        stop: Callable[[], dict[str, Any]],
        snapshot: Callable[[], dict[str, Any]],
    ) -> None:
        # Handler 通过闭包持有这些业务回调。HTTP 层只处理协议，不依赖 ROS2。
        handler = _handler_factory(
            status=status,
            operation=operation,
            submit=submit,
            follow_operation=follow_operation,
            submit_follow=submit_follow,
            cancel_follow=cancel_follow,
            stop=stop,
            snapshot=snapshot,
        )
        # 每个 HTTP 请求可由独立线程处理；真正的运动状态由 node.py 负责同步。
        self._server = ThreadingHTTPServer((host, port), handler)
        self._thread = Thread(
            target=self._server.serve_forever,
            name="xuegecar-agent-http",
            daemon=True,
        )

    @property
    def address(self) -> tuple[str, int]:
        """返回实际监听地址，测试时端口可设为 0。"""

        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        """启动后台 HTTP 线程。"""

        self._thread.start()

    def close(self) -> None:
        """停止服务器并等待后台线程退出。"""

        self._server.shutdown()
        self._server.server_close()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)


def _handler_factory(
    *,
    status: Callable[[], dict[str, Any]],
    operation: Callable[[str], dict[str, Any] | None],
    submit: Callable[[dict[str, Any]], dict[str, Any]],
    follow_operation: Callable[[str], dict[str, Any] | None],
    submit_follow: Callable[[dict[str, Any]], dict[str, Any]],
    cancel_follow: Callable[[str], dict[str, Any]],
    stop: Callable[[], dict[str, Any]],
    snapshot: Callable[[], dict[str, Any]],
) -> type[BaseHTTPRequestHandler]:
    # 工厂把节点提供的回调封装进 Handler 类，避免使用全局节点对象。
    class Handler(BaseHTTPRequestHandler):
        server_version = "XuegecarAgentGateway/0.1"

        def do_GET(self) -> None:
            # 查询接口只读取节点维护的快照，不直接访问 ROS2 控制器。
            if self.path == "/v1/robot/status":
                self._write_json(200, status())
                return
            match = OPERATION_PATH.fullmatch(self.path)
            if match:
                # Agent 会对路径段执行 URL 编码，例如计划动作 ID 中的冒号会变成
                # %3A。查询状态前必须还原原始 operation_id，才能命中提交时保存的记录。
                operation_id = unquote(match.group(1))
                result = operation(operation_id)
                if result is None:
                    self._write_json(
                        404, {"error_code": "NOT_FOUND", "error": "未知 operation_id"}
                    )
                else:
                    self._write_json(200, result)
                return
            match = FOLLOW_PATH.fullmatch(self.path)
            if match:
                operation_id = unquote(match.group(1))
                result = follow_operation(operation_id)
                if result is None:
                    self._write_json(
                        404, {"error_code": "NOT_FOUND", "error": "未知 operation_id"}
                    )
                else:
                    self._write_json(200, result)
                return
            if self.path == "/v1/camera/snapshot":
                try:
                    # 抓取相机最新帧并落盘；无帧、断流等以领域错误码返回。
                    self._write_json(200, snapshot())
                except GatewayRejected as error:
                    self._write_rejection(error)
                return
            self._write_json(404, {"error_code": "NOT_FOUND", "error": "接口不存在"})

        def do_POST(self) -> None:
            try:
                if self.path == "/v1/motions":
                    # 读取 Agent 的动作 JSON，再通过 submit 回调交给 ROS2 节点。
                    # 202 表示动作已被接受，最终结果仍需通过 GET 接口轮询。
                    self._write_json(202, submit(self._read_json()))
                    return
                if self.path == "/v1/follow-tasks":
                    self._write_json(202, submit_follow(self._read_json()))
                    return
                match = CANCEL_FOLLOW_PATH.fullmatch(self.path)
                if match:
                    self._write_json(200, cancel_follow(unquote(match.group(1))))
                    return
                if self.path == "/v1/stop":
                    # 停车也走节点回调，以保证取消动作与发布零速度在 ROS 主线程处理。
                    self._write_json(200, stop())
                    return
                self._write_json(
                    404, {"error_code": "NOT_FOUND", "error": "接口不存在"}
                )
            except GatewayRejected as error:
                self._write_rejection(error)
            except (
                json.JSONDecodeError,
                TypeError,
                UnicodeDecodeError,
                ValueError,
            ) as error:
                self._write_json(
                    400, {"error_code": "INVALID_JSON", "error": str(error)}
                )
            except TimeoutError as error:
                self._write_json(
                    503, {"error_code": "GATEWAY_TIMEOUT", "error": str(error)}
                )

        def _read_json(self) -> dict[str, Any]:
            # 先校验长度再读取请求体，防止无界输入占用过多内存。
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("缺少 Content-Length")
            length = int(raw_length)
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("请求体大小无效")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("JSON 顶层必须是对象")
            return payload

        def _write_rejection(self, error: GatewayRejected) -> None:
            self._write_json(
                _rejection_status(error.code),
                {"error_code": error.code, "error": str(error)},
            )

        def _write_json(self, status_code: int, payload: dict[str, Any]) -> None:
            # HTTP 回复统一为 UTF-8 JSON；wfile.write() 才是真正把正文发回 Agent。
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(
                "utf-8"
            )
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            # 关闭 BaseHTTPRequestHandler 默认写入 stderr 的逐请求日志。
            return

    return Handler

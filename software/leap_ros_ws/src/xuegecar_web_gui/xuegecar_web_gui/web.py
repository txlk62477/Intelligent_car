"""FastAPI Web 层：静态页面、MJPEG 摄像头流、单客户端控制 WebSocket。"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import rclpy
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from rclpy.executors import MultiThreadedExecutor

from xuegecar_web_gui.node import WebGuiNode

_STATIC_DIR = Path(__file__).resolve().parent / "static"

# WebSocket 关闭码：4008 = 已被其他客户端占用。
WS_CLOSE_BUSY = 4008


def create_app(node: WebGuiNode) -> FastAPI:
    app = FastAPI(title="xuegecar_web_gui", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/stream")
    async def stream(token: str = Query(default="")) -> StreamingResponse:
        if not node.token_valid(token):
            return StreamingResponse(
                iter([b""]),
                media_type="image/jpeg",
                status_code=403,
            )
        return StreamingResponse(
            _frame_generator(node, token),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.websocket("/ws")
    async def ws_control(websocket: WebSocket) -> None:
        await websocket.accept()
        owner_ip = websocket.client.host or "unknown"
        token, busy_owner = node.try_acquire_session(owner_ip)
        if token is None:
            # 单客户端策略：直接拒绝并告知当前控制者。
            await websocket.send_json({"type": "busy", "owner_ip": busy_owner})
            await websocket.close(code=WS_CLOSE_BUSY, reason=f"busy:{busy_owner}")
            return

        await websocket.send_json(
            {
                "type": "welcome",
                "token": token,
                "state": node.state_snapshot(),
            }
        )

        async def push_state() -> None:
            while True:
                await asyncio.sleep(0.2)
                if not node.token_valid(token):
                    break
                try:
                    await websocket.send_json(node.state_snapshot())
                except Exception:  # noqa: BLE001
                    break

        pusher = asyncio.create_task(push_state())
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                except asyncio.TimeoutError:
                    # 空闲超时检查：会话超过 session_timeout 无活动 -> 释放。
                    if node.session_expired(token):
                        break
                    continue
                except WebSocketDisconnect:
                    break
                if not node.touch_session(token):
                    break
                await _handle_message(node, websocket, raw)
        finally:
            pusher.cancel()
            node.release_session(token)

    return app


async def _handle_message(node: WebGuiNode, websocket: WebSocket, raw: str) -> None:
    try:
        message = json.loads(raw)
    except (ValueError, TypeError):
        await websocket.send_json({"type": "error", "message": "invalid json"})
        return

    msg_type = message.get("type")
    if msg_type == "cmd":
        node.set_command(
            float(message.get("linear", 0.0)),
            float(message.get("angular", 0.0)),
        )
    elif msg_type == "speed":
        node.set_speed_limits(
            float(message.get("max_linear", 0.3)),
            float(message.get("max_angular", 1.0)),
        )
    elif msg_type == "stop":
        node.request_stop()
    elif msg_type == "estop":
        node.enqueue_estop()
        await websocket.send_json({"type": "ack", "action": "estop"})
    elif msg_type == "unlock":
        node.enqueue_unlock()
        await websocket.send_json({"type": "ack", "action": "unlock"})
    elif msg_type == "ping":
        pass
    else:
        await websocket.send_json(
            {"type": "error", "message": f"unknown type: {msg_type}"}
        )


async def _frame_generator(node: WebGuiNode, token: str):
    last_seq = 0
    while node.token_valid(token):
        frame = node.wait_frame(last_seq, timeout=1.0)
        if frame is None:
            continue
        jpeg_bytes, last_seq = frame
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(jpeg_bytes)).encode() + b"\r\n\r\n"
            + jpeg_bytes + b"\r\n"
        )


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = WebGuiNode()

    app = create_app(node)
    config = uvicorn.Config(
        app,
        host=node.host,
        port=node.port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    node.get_logger().info(
        f"Web 服务已启动: http://{node.host}:{node.port} "
        f"（手机浏览器输入 http://<本机IP>:{node.port}）"
    )

    executor = MultiThreadedExecutor(num_threads=3)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        executor.shutdown(timeout_sec=1.0)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

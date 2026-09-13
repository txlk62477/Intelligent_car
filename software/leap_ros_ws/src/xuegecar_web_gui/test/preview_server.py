#!/usr/bin/env python3
"""独立 UI 预览服务（不依赖 ROS）。

用真实的 ``xuegecar_web_gui/static`` 前端搭配一个假后端，便于在桌面浏览器或
无头浏览器里按任意视口尺寸检查页面布局，不需要启动 rclpy / 底盘 / 摄像头。

    .venv/bin/python test/preview_server.py --port 8080

可选开关用于复现各种界面状态：

    --no-camera    摄像头无信号（占位图 + 黄色 pill）
    --estop        急停锁止状态
    --busy         单客户端占用（第二个连接显示遮罩）
    --no-battery   电池信息缺失

无头截图见 ``test/shoot_ui.py``。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

_STATIC_DIR = Path(__file__).resolve().parent.parent / "xuegecar_web_gui" / "static"


class MockState:
    """极简状态机：只负责让前端显示出有代表性的界面。"""

    def __init__(self, *, camera: bool, battery: bool, estop: bool) -> None:
        self.camera = camera
        self.battery = battery
        self.estop_locked = estop
        self.linear = 0.0
        self.angular = 0.0
        self.max_linear = 0.3
        self.max_angular = 1.0
        self.started = time.monotonic()
        self.last_frame = 0.0
        self.streams = 0

    def snapshot(self) -> dict:
        """必须与 WebGuiNode.state_snapshot() 的字段（含 type）保持一致。"""
        now = time.monotonic()
        # 前端把 camera_age < 0 或 > 3s 视为无信号：只有正在推流才算在线。
        if self.camera and self.streams and self.last_frame:
            age: float | None = now - self.last_frame
        else:
            age = -1.0
        return {
            "type": "state",
            "estop_locked": self.estop_locked,
            "battery_percent": 87.0 if self.battery else None,
            "battery_voltage": 12.34 if self.battery else None,
            "camera_age": age,
            "camera_fps": 15.0 if (self.camera and self.streams) else 0.0,
            "cmd_linear": self.linear,
            "cmd_angular": self.angular,
            "odom_linear": self.linear * 0.95,
            "odom_angular": self.angular * 0.95,
            "max_linear": self.max_linear,
            "max_angular": self.max_angular,
            "owner_ip": "127.0.0.1",
        }


def _jpeg_frame(index: int, width: int = 640, height: int = 480) -> bytes:
    """合成一帧带编号和移动标记的测试图，用来确认画面在刷新。"""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), (18, 26, 42))
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 40):
        draw.line([(x, 0), (x, height)], fill=(30, 45, 70), width=1)
    for y in range(0, height, 40):
        draw.line([(0, y), (width, y)], fill=(30, 45, 70), width=1)
    # 画面中心十字，方便判断 object-fit 的裁切方式。
    draw.line([(width // 2, 0), (width // 2, height)], fill=(239, 68, 68), width=2)
    draw.line([(0, height // 2), (width, height // 2)], fill=(239, 68, 68), width=2)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(47, 128, 237), width=4)
    for corner in ((0, 0), (width - 60, 0), (0, height - 60), (width - 60, height - 60)):
        draw.rectangle(
            [corner[0] + 4, corner[1] + 4, corner[0] + 56, corner[1] + 56],
            outline=(34, 197, 94),
            width=3,
        )
    phase = (index % 60) / 60.0
    cx = int(width * (0.5 + 0.35 * math.cos(phase * 2 * math.pi)))
    cy = int(height * (0.5 + 0.35 * math.sin(phase * 2 * math.pi)))
    draw.ellipse([cx - 26, cy - 26, cx + 26, cy + 26], fill=(245, 158, 11))
    draw.text((12, height // 2 + 8), f"PREVIEW #{index:05d}", fill=(226, 232, 240))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()


def create_app(state: MockState, *, busy: bool) -> FastAPI:
    app = FastAPI(title="xuegecar_web_gui preview", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/debug-state")
    async def debug_state() -> dict:
        """给截图脚本排查用：确认推流/状态机是否真的在工作。"""
        return {"streams": state.streams, "last_frame_age": (
            time.monotonic() - state.last_frame if state.last_frame else None
        ), **state.snapshot()}

    @app.get("/stream")
    async def stream(token: str = Query(default="")) -> StreamingResponse:
        if token != "preview":
            return StreamingResponse(iter([b""]), media_type="image/jpeg", status_code=403)
        return StreamingResponse(
            _frames(state), media_type="multipart/x-mixed-replace; boundary=frame"
        )

    @app.websocket("/ws")
    async def ws_control(websocket: WebSocket) -> None:
        await websocket.accept()
        if busy:
            await websocket.send_json({"type": "busy", "owner_ip": "192.168.1.42"})
            await websocket.close(code=4008, reason="busy:192.168.1.42")
            return
        await websocket.send_json(
            {"type": "welcome", "token": "preview", "state": state.snapshot()}
        )

        async def push() -> None:
            while True:
                await asyncio.sleep(0.2)
                try:
                    await websocket.send_json(state.snapshot())
                except Exception:  # noqa: BLE001
                    return

        pusher = asyncio.create_task(push())
        try:
            while True:
                raw = await websocket.receive_text()
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "cmd":
                    state.linear = float(message.get("linear", 0.0))
                    state.angular = float(message.get("angular", 0.0))
                elif kind == "speed":
                    state.max_linear = float(message.get("max_linear", 0.3))
                    state.max_angular = float(message.get("max_angular", 1.0))
                elif kind == "stop":
                    state.linear = state.angular = 0.0
                elif kind == "estop":
                    state.estop_locked = True
                elif kind == "unlock":
                    state.estop_locked = False
        except WebSocketDisconnect:
            pass
        finally:
            pusher.cancel()

    return app


async def _frames(state: MockState):
    index = 0
    state.streams += 1
    try:
        while True:
            payload = _jpeg_frame(index)
            index += 1
            state.last_frame = time.monotonic()
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\n\r\n"
                + payload
                + b"\r\n"
            )
            await asyncio.sleep(1 / 15)
    finally:
        state.streams -= 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-camera", action="store_true", help="模拟摄像头无信号")
    parser.add_argument("--no-battery", action="store_true", help="模拟电池信息缺失")
    parser.add_argument("--estop", action="store_true", help="进入急停锁止状态")
    parser.add_argument("--busy", action="store_true", help="模拟控制权被占用")
    args = parser.parse_args()

    state = MockState(
        camera=not args.no_camera, battery=not args.no_battery, estop=args.estop
    )
    app = create_app(state, busy=args.busy)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()

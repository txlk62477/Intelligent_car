"""测试用 WS 客户端：验证 welcome/busy/cmd 下发。"""

import asyncio
import json
import sys

import websockets

TOKEN_FILE = "/home/lk/car/software/leap_ros_ws/token.txt"


async def main():
    # 客户端 1：应获得控制权。
    ws1 = await websockets.connect("ws://127.0.0.1:8000/ws")
    welcome = json.loads(await ws1.recv())
    assert welcome["type"] == "welcome", welcome
    token = welcome["token"]
    print(f"client1: welcome, token={token[:8]}…")
    with open(TOKEN_FILE, "w") as f:
        f.write(token)

    # 客户端 2：应被 busy 拒绝。
    ws2 = await websockets.connect("ws://127.0.0.1:8000/ws")
    busy = json.loads(await ws2.recv())
    print(f"client2: {busy}")
    try:
        await asyncio.wait_for(ws2.recv(), timeout=3)
        print("client2: ERROR - not closed")
    except websockets.exceptions.ConnectionClosed as exc:
        print(f"client2: closed code={exc.code} reason={exc.reason}")

    # 客户端 1 持续发 cmd 3 秒（10Hz），然后停止。
    for _ in range(30):
        await ws1.send(json.dumps({"type": "cmd", "linear": 0.25, "angular": -0.5}))
        await asyncio.sleep(0.1)
    await ws1.send(json.dumps({"type": "stop"}))
    print("client1: sent cmd 3s (0.25, -0.5), then stop")

    # 再保持会话 8 秒，供外部 curl 验证 /stream。
    for _ in range(40):
        await ws1.send(json.dumps({"type": "ping"}))
        await asyncio.sleep(0.2)
    await ws1.close()
    print("client1: closed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print("ASSERT FAILED:", exc)
        sys.exit(1)

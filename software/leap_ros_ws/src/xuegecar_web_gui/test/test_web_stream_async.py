"""Regression test: camera frame waits must not block WebSocket work."""

import asyncio
import time

from xuegecar_web_gui.web import _frame_generator


class DelayedFrameNode:
    """Minimal frame source that behaves like a low-rate camera callback."""

    @staticmethod
    def token_valid(_token: str) -> bool:
        return True

    @staticmethod
    def wait_frame(
        _last_seq: int, timeout: float  # noqa: ARG004
    ) -> tuple[bytes, int]:
        time.sleep(0.15)
        return b'jpeg', 1


def test_waiting_for_camera_frame_does_not_starve_event_loop():
    async def scenario() -> float:
        generator = _frame_generator(DelayedFrameNode(), 'token')
        started = time.monotonic()

        async def websocket_work() -> float:
            await asyncio.sleep(0.01)
            return time.monotonic() - started

        _, websocket_delay = await asyncio.gather(
            generator.__anext__(),
            websocket_work(),
        )
        await generator.aclose()
        return websocket_delay

    # The camera takes 150 ms, but unrelated WebSocket work should still run
    # after roughly 10 ms. A synchronous wait in the async generator makes
    # this exceed 150 ms and reproduces the browser-control freeze.
    assert asyncio.run(scenario()) < 0.08

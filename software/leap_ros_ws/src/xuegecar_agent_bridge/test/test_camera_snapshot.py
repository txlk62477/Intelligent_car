"""CameraSnapshotStore 缓存、过期与落盘逻辑测试。"""

from __future__ import annotations

import time

import pytest

from xuegecar_agent_bridge.camera_snapshot import CameraSnapshotStore
from xuegecar_agent_bridge.errors import GatewayRejected


def test_capture_writes_latest_jpeg_and_reports_file(tmp_path):
    store = CameraSnapshotStore(tmp_path)
    store.store(b"\xff\xd8fake-jpeg", "jpeg")

    result = store.capture()

    assert result["status"] == "captured"
    assert result["format"] == "jpeg"
    assert result["frame_age_seconds"] >= 0.0
    assert result["path"].startswith(str(tmp_path))
    assert result["path"].endswith(".jpg")
    assert open(result["path"], "rb").read() == b"\xff\xd8fake-jpeg"


def test_capture_uses_latest_frame_only(tmp_path):
    store = CameraSnapshotStore(tmp_path)
    store.store(b"old", "jpeg")
    store.store(b"new", "jpeg")

    result = store.capture()

    assert open(result["path"], "rb").read() == b"new"


def test_capture_without_frame_rejects_with_no_frame(tmp_path):
    store = CameraSnapshotStore(tmp_path)

    with pytest.raises(GatewayRejected) as raised:
        store.capture()
    assert raised.value.code == "NO_FRAME"


def test_capture_rejects_stale_frame(tmp_path):
    store = CameraSnapshotStore(tmp_path, max_age=0.05)
    store.store(b"frame", "jpeg")
    time.sleep(0.08)

    with pytest.raises(GatewayRejected) as raised:
        store.capture()
    assert raised.value.code == "STALE_FRAME"


def test_store_ignores_non_jpeg_formats(tmp_path):
    store = CameraSnapshotStore(tmp_path)
    store.store(b"raw-bgr8", "bgr8")

    with pytest.raises(GatewayRejected) as raised:
        store.capture()
    assert raised.value.code == "NO_FRAME"


def test_capture_without_output_dir_rejects(tmp_path):
    store = CameraSnapshotStore(None)

    with pytest.raises(GatewayRejected) as raised:
        store.capture()
    assert raised.value.code == "UNAVAILABLE"

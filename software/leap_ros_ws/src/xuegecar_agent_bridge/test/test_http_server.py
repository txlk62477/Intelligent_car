import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from xuegecar_agent_bridge.errors import GatewayRejected
from xuegecar_agent_bridge.http_server import GatewayHttpServer


def request_json(base_url, path, *, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(
        base_url + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urlopen(request, timeout=2.0) as response:
        return response.status, json.load(response)


def test_http_routes_status_submit_operation_and_stop():
    operations = {}

    def submit(payload):
        operations[payload["operation_id"]] = {**payload, "status": "RUNNING"}
        return operations[payload["operation_id"]]

    server = GatewayHttpServer(
        "127.0.0.1",
        0,
        status=lambda: {"online": True},
        operation=operations.get,
        submit=submit,
        follow_operation=lambda operation_id: operations.get(operation_id),
        submit_follow=submit,
        cancel_follow=lambda operation_id: operations[operation_id],
        stop=lambda: {"gateway_status": "IDLE"},
        snapshot=lambda: {
            "status": "captured",
            "path": "/tmp/snapshot_1.jpg",
            "format": "jpeg",
        },
        detections=lambda: {"status": "DETECTED", "detections": []},
        navigation_status=lambda: {"status": "READY", "map_id": "sha256:test"},
        navigation_operation=lambda operation_id: operations.get(operation_id),
        preflight_navigation=lambda payload: {**payload, "status": "READY"},
        submit_navigation=submit,
        cancel_navigation=lambda operation_id: operations[operation_id],
    )
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        assert request_json(base_url, "/v1/robot/status") == (200, {"online": True})
        status, body = request_json(
            base_url,
            "/v1/motions",
            payload={
                # Workflow 使用“计划 ID:动作下标”，Agent 查询时会把冒号编码为 %3A。
                "operation_id": "plan-1:0",
                "type": "forward",
                "mode": "time",
                "value": 1,
            },
        )
        assert status == 202
        assert body["status"] == "RUNNING"
        assert request_json(base_url, "/v1/motions/plan-1%3A0")[1]["status"] == (
            "RUNNING"
        )
        follow_status, _follow = request_json(
            base_url,
            "/v1/follow-tasks",
            payload={
                "operation_id": "follow-1",
                "target_label": "cup",
                "timeout_seconds": 60,
            },
        )
        assert follow_status == 202
        assert request_json(base_url, "/v1/follow-tasks/follow-1")[1][
            "target_label"
        ] == "cup"
        assert request_json(
            base_url,
            "/v1/follow-tasks/follow-1/cancel",
            payload={},
        )[1]["operation_id"] == "follow-1"
        assert request_json(base_url, "/v1/stop", payload={})[1] == {
            "gateway_status": "IDLE"
        }
        assert request_json(base_url, "/v1/navigation/status")[1]["status"] == (
            "READY"
        )
        assert request_json(
            base_url,
            "/v1/navigation/preflight",
            payload={"map_id": "sha256:test", "pose": {"x": 1, "y": 2, "yaw": 0}},
        )[1]["status"] == "READY"
        assert request_json(
            base_url,
            "/v1/navigation-tasks",
            payload={"operation_id": "nav-1"},
        )[0] == 202
        assert request_json(base_url, "/v1/navigation-tasks/nav-1")[1][
            "operation_id"
        ] == "nav-1"
        assert request_json(
            base_url, "/v1/navigation-tasks/nav-1/cancel", payload={}
        )[0] == 200
    finally:
        server.close()


def test_http_camera_snapshot_route_and_rejection():
    def snapshot():
        raise GatewayRejected("NO_FRAME", "尚未收到相机帧")

    server = GatewayHttpServer(
        "127.0.0.1",
        0,
        status=dict,
        operation=lambda _operation_id: None,
        submit=dict,
        follow_operation=lambda _operation_id: None,
        submit_follow=dict,
        cancel_follow=lambda _operation_id: {},
        stop=dict,
        snapshot=snapshot,
        detections=dict,
    )
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        try:
            request_json(base_url, "/v1/camera/snapshot")
        except HTTPError as error:
            assert error.code == 503
            assert json.load(error)["error_code"] == "NO_FRAME"
        else:
            raise AssertionError("无相机帧时应返回 HTTP 503")
    finally:
        server.close()


def test_http_camera_snapshot_returns_captured_frame():
    server = GatewayHttpServer(
        "127.0.0.1",
        0,
        status=dict,
        operation=lambda _operation_id: None,
        submit=dict,
        follow_operation=lambda _operation_id: None,
        submit_follow=dict,
        cancel_follow=lambda _operation_id: {},
        stop=dict,
        snapshot=lambda: {"status": "captured", "path": "/tmp/frame.jpg"},
        detections=dict,
    )
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        status, body = request_json(base_url, "/v1/camera/snapshot")
        assert status == 200
        assert body["path"] == "/tmp/frame.jpg"
    finally:
        server.close()


def test_http_perception_detections_route():
    server = GatewayHttpServer(
        "127.0.0.1",
        0,
        status=dict,
        operation=lambda _operation_id: None,
        submit=dict,
        follow_operation=lambda _operation_id: None,
        submit_follow=dict,
        cancel_follow=lambda _operation_id: {},
        stop=dict,
        snapshot=dict,
        detections=lambda: {
            "status": "DETECTED",
            "detections": [{"label": "cup", "score": 0.9, "position": "中央"}],
        },
    )
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        status, body = request_json(base_url, "/v1/perception/detections")
        assert status == 200
        assert body["status"] == "DETECTED"
        assert body["detections"][0]["label"] == "cup"
    finally:
        server.close()


def test_http_maps_domain_rejection_to_json_error():
    def reject(_payload):
        raise GatewayRejected("BUSY", "已有动作正在执行")

    server = GatewayHttpServer(
        "127.0.0.1",
        0,
        status=dict,
        operation=lambda _operation_id: None,
        submit=reject,
        follow_operation=lambda _operation_id: None,
        submit_follow=reject,
        cancel_follow=lambda _operation_id: {},
        stop=dict,
        snapshot=dict,
        detections=dict,
    )
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        try:
            request_json(base_url, "/v1/motions", payload={"operation_id": "op-1"})
        except HTTPError as error:
            assert error.code == 409
            assert json.load(error)["error_code"] == "BUSY"
        else:
            raise AssertionError("应返回 HTTP 409")
    finally:
        server.close()

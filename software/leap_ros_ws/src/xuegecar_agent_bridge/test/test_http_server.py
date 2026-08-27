import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from xuegecar_agent_bridge.controller import MotionRejected
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
        stop=lambda: {"gateway_status": "IDLE"},
    )
    server.start()
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        assert request_json(base_url, "/v1/robot/status") == (200, {"online": True})
        status, body = request_json(
            base_url,
            "/v1/motions",
            payload={
                "operation_id": "op-1",
                "type": "forward",
                "mode": "time",
                "value": 1,
            },
        )
        assert status == 202
        assert body["status"] == "RUNNING"
        assert request_json(base_url, "/v1/motions/op-1")[1]["status"] == "RUNNING"
        assert request_json(base_url, "/v1/stop", payload={})[1] == {
            "gateway_status": "IDLE"
        }
    finally:
        server.close()


def test_http_maps_domain_rejection_to_json_error():
    def reject(_payload):
        raise MotionRejected("BUSY", "已有动作正在执行")

    server = GatewayHttpServer(
        "127.0.0.1",
        0,
        status=dict,
        operation=lambda _operation_id: None,
        submit=reject,
        stop=dict,
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

"""ROS2 Action 与本机 HTTP 之间的轻量 Gateway。"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Any

import rclpy
from leap_interfaces.action import ExecuteMotion, FollowTarget
from leap_interfaces.msg import Detections
from leap_interfaces.srv import SetPerception
from nav_msgs.msg import Odometry
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage
from std_srvs.srv import Trigger

from xuegecar_agent_bridge.camera_snapshot import CameraSnapshotStore
from xuegecar_agent_bridge.detections import (
    NO_FRAME,
    TIMEOUT,
    DetectionCache,
    box_position,
)
from xuegecar_agent_bridge.errors import GatewayRejected
from xuegecar_agent_bridge.http_server import GatewayHttpServer

TERMINAL_STATUSES = {
    "SUCCEEDED",
    "FAILED",
    "TIMED_OUT",
    "CANCELLED",
    "ODOM_TIMEOUT",
}

COCO_TARGETS = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana",
    "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza",
    "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table",
    "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock",
    "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)


class AgentGatewayNode(Node):
    """把稳定 JSON 请求适配为可反馈、可取消的 ROS2 Action。"""

    def __init__(self) -> None:
        super().__init__("xuegecar_agent_bridge")
        self.declare_parameter("http_host", "127.0.0.1")
        self.declare_parameter("http_port", 8765)
        self.declare_parameter("odom_topic", "/odometry/filtered")
        self.declare_parameter("odom_timeout", 1.0)
        self.declare_parameter("action_timeout", 2.0)
        self.declare_parameter("history_size", 100)
        self.declare_parameter("follow_default_timeout", 60.0)
        self.declare_parameter("follow_max_timeout", 300.0)
        self.declare_parameter("supported_target_labels", list(COCO_TARGETS))
        self.declare_parameter("camera_topic", "/camera/image_raw/compressed")
        self.declare_parameter("snapshot_dir", "")
        self.declare_parameter("snapshot_max_age", 5.0)
        self.declare_parameter("detections_topic", "/vision/detections")
        self.declare_parameter("detections_max_age", 1.5)
        self.declare_parameter("detections_min_score", 0.35)
        self.declare_parameter("detections_max_items", 8)
        self.declare_parameter("detections_probe_timeout", 5.0)
        self.declare_parameter("detections_probe_runtime", 20.0)

        self._callbacks = ReentrantCallbackGroup()
        self._lock = Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self._active_operation_id: str | None = None
        self._active_kind: str | None = None
        self._goal_handles: dict[str, Any] = {}
        self._last_odom: dict[str, Any] | None = None
        self._last_odom_at: float | None = None
        self._history_size = int(self.get_parameter("history_size").value)
        self._action_timeout = float(self.get_parameter("action_timeout").value)
        self._follow_default_timeout = float(
            self.get_parameter("follow_default_timeout").value
        )
        self._follow_max_timeout = float(
            self.get_parameter("follow_max_timeout").value
        )
        self._supported_targets = {
            str(value)
            for value in self.get_parameter("supported_target_labels").value
        }

        self._motion_client = ActionClient(
            self,
            ExecuteMotion,
            "/motion/execute",
            callback_group=self._callbacks,
        )
        self._follow_client = ActionClient(
            self,
            FollowTarget,
            "/motion/follow_target",
            callback_group=self._callbacks,
        )
        self._emergency_client = self.create_client(
            Trigger,
            "/motion/emergency_stop",
            callback_group=self._callbacks,
        )
        self.create_subscription(
            Odometry,
            str(self.get_parameter("odom_topic").value),
            self._on_odom,
            qos_profile_sensor_data,
            callback_group=self._callbacks,
        )
        snapshot_dir = str(self.get_parameter("snapshot_dir").value).strip()
        self._snapshot_store = CameraSnapshotStore(
            Path(snapshot_dir).expanduser() if snapshot_dir else None,
            max_age=float(self.get_parameter("snapshot_max_age").value),
        )
        self.create_subscription(
            CompressedImage,
            str(self.get_parameter("camera_topic").value),
            self._on_camera_frame,
            _video_qos(),
            callback_group=self._callbacks,
        )
        self._detection_cache = DetectionCache(
            max_age=float(self.get_parameter("detections_max_age").value),
            min_score=float(self.get_parameter("detections_min_score").value),
            max_items=int(self.get_parameter("detections_max_items").value),
        )
        self.create_subscription(
            Detections,
            str(self.get_parameter("detections_topic").value),
            self._on_detections,
            qos_profile_sensor_data,
            callback_group=self._callbacks,
        )
        self._perception_client = self.create_client(
            SetPerception,
            "/perception/set_enabled",
            callback_group=self._callbacks,
        )
        self._detections_probe_timeout = float(
            self.get_parameter("detections_probe_timeout").value
        )
        self._detections_probe_runtime = float(
            self.get_parameter("detections_probe_runtime").value
        )

        host = str(self.get_parameter("http_host").value)
        port = int(self.get_parameter("http_port").value)
        self._http = GatewayHttpServer(
            host,
            port,
            status=self._read_status,
            operation=lambda operation_id: self._read_operation(operation_id, "motion"),
            submit=self._submit_motion,
            follow_operation=lambda operation_id: self._read_operation(
                operation_id, "follow"
            ),
            submit_follow=self._submit_follow,
            cancel_follow=self._cancel_follow,
            stop=self._stop,
            snapshot=self._snapshot_store.capture,
            detections=self._read_detections,
        )
        self._http.start()
        self.get_logger().info(f"Agent Gateway listening on http://{host}:{port}")

    def _submit_motion(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = _validate_motion(payload)
        goal = ExecuteMotion.Goal()
        goal.operation_id = normalized["operation_id"]
        goal.type = normalized["type"]
        goal.mode = normalized["mode"]
        goal.value = normalized["value"]
        record = {
            **normalized,
            "kind": "motion",
            "status": "PENDING",
            "progress": 0.0,
            "error_code": None,
            "error": None,
        }
        return self._submit_action(
            record,
            self._motion_client,
            goal,
            self._motion_feedback,
            self._motion_result,
        )

    def _submit_follow(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = self._validate_follow(payload)
        goal = FollowTarget.Goal()
        goal.operation_id = normalized["operation_id"]
        goal.target_label = normalized["target_label"]
        goal.timeout_seconds = normalized["timeout_seconds"]
        record = {
            **normalized,
            "kind": "follow",
            "status": "STARTING",
            "elapsed_seconds": 0.0,
            "target_visible": False,
            "confidence": 0.0,
            "center_error": 0.0,
            "area_ratio": 0.0,
            "command": {"linear_x": 0.0, "angular_z": 0.0},
            "error_code": None,
            "error": None,
        }
        return self._submit_action(
            record,
            self._follow_client,
            goal,
            self._follow_feedback,
            self._follow_result,
        )

    def _submit_action(
        self,
        record: dict[str, Any],
        client: Any,
        goal: Any,
        feedback_callback: Callable[[str, Any], None],
        result_callback: Callable[[str, Any], None],
    ) -> dict[str, Any]:
        operation_id = str(record["operation_id"])
        kind = str(record["kind"])
        request_identity = {
            key: value
            for key, value in record.items()
            if key
            in {
                "operation_id",
                "kind",
                "type",
                "mode",
                "value",
                "target_label",
                "timeout_seconds",
            }
        }
        with self._lock:
            previous = self._records.get(operation_id)
            if previous is not None:
                if previous.get("_request") != request_identity:
                    raise GatewayRejected(
                        "ID_CONFLICT", "operation_id 已用于不同任务"
                    )
                return _public(previous)
            if self._active_operation_id is not None:
                raise GatewayRejected("BUSY", "已有任务正在执行")
            record["_request"] = request_identity
            self._records[operation_id] = record
            self._active_operation_id = operation_id
            self._active_kind = kind

        if not client.wait_for_server(timeout_sec=self._action_timeout):
            self._reject_pending(operation_id)
            raise GatewayRejected("UNAVAILABLE", "运动控制 Action Server 不可用")
        future = client.send_goal_async(
            goal,
            feedback_callback=lambda message: feedback_callback(operation_id, message),
        )
        if not _wait_future(future, self._action_timeout):
            self._reject_pending(operation_id)
            raise GatewayRejected("GATEWAY_TIMEOUT", "提交 ROS2 Action 超时")
        goal_handle = future.result()
        if goal_handle is None or not goal_handle.accepted:
            self._reject_pending(operation_id)
            raise GatewayRejected("BUSY", "运动控制节点拒绝任务，可能已有任务运行")
        with self._lock:
            self._goal_handles[operation_id] = goal_handle
            current = self._records[operation_id]
            if current["status"] == "PENDING":
                current["status"] = "RUNNING"
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(
            lambda completed: result_callback(operation_id, completed)
        )
        with self._lock:
            return _public(self._records[operation_id])

    def _motion_feedback(self, operation_id: str, message: Any) -> None:
        feedback = message.feedback
        with self._lock:
            record = self._records.get(operation_id)
            if record is not None:
                record["status"] = feedback.status
                record["progress"] = float(feedback.progress)

    def _follow_feedback(self, operation_id: str, message: Any) -> None:
        self._update_follow_record(operation_id, message.feedback)

    def _motion_result(self, operation_id: str, future: Any) -> None:
        try:
            result = future.result().result
            values = {
                "status": result.status,
                "progress": float(result.progress),
                "error_code": result.error_code or None,
                "error": result.error or None,
            }
        except Exception as error:  # noqa: BLE001
            values = {
                "status": "FAILED",
                "error_code": "INVALID_ACTION_RESULT",
                "error": str(error),
            }
        self._finish_record(operation_id, values)

    def _follow_result(self, operation_id: str, future: Any) -> None:
        try:
            result = future.result().result
            self._update_follow_record(operation_id, result)
            values = {
                "status": result.status,
                "error_code": result.error_code or None,
                "error": result.error or None,
            }
        except Exception as error:  # noqa: BLE001
            values = {
                "status": "FAILED",
                "error_code": "INVALID_ACTION_RESULT",
                "error": str(error),
            }
        self._finish_record(operation_id, values)

    def _update_follow_record(self, operation_id: str, value: Any) -> None:
        with self._lock:
            record = self._records.get(operation_id)
            if record is None:
                return
            record.update(
                {
                    "status": value.status,
                    "elapsed_seconds": round(float(value.elapsed_seconds), 3),
                    "target_visible": bool(value.target_visible),
                    "confidence": round(float(value.confidence), 4),
                    "center_error": round(float(value.center_error), 4),
                    "area_ratio": round(float(value.area_ratio), 4),
                    "command": {
                        "linear_x": round(float(value.linear_x), 4),
                        "angular_z": round(float(value.angular_z), 4),
                    },
                }
            )

    def _finish_record(self, operation_id: str, values: dict[str, Any]) -> None:
        with self._lock:
            record = self._records.get(operation_id)
            if record is not None:
                record.update(values)
            self._goal_handles.pop(operation_id, None)
            if self._active_operation_id == operation_id:
                self._active_operation_id = None
                self._active_kind = None
            self._trim_history_locked()

    def _reject_pending(self, operation_id: str) -> None:
        with self._lock:
            self._records.pop(operation_id, None)
            if self._active_operation_id == operation_id:
                self._active_operation_id = None
                self._active_kind = None

    def _read_operation(self, operation_id: str, kind: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(operation_id)
            if record is None or record.get("kind") != kind:
                return None
            return _public(record)

    def _cancel_follow(self, operation_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._records.get(operation_id)
            if record is None or record.get("kind") != "follow":
                raise GatewayRejected("NOT_FOUND", "未知 operation_id")
            if record.get("status") in TERMINAL_STATUSES:
                return _public(record)
            goal_handle = self._goal_handles.get(operation_id)
        if goal_handle is None:
            raise GatewayRejected("UNAVAILABLE", "任务尚未获得可取消句柄")
        future = goal_handle.cancel_goal_async()
        if not _wait_future(future, self._action_timeout):
            raise GatewayRejected("GATEWAY_TIMEOUT", "取消任务超时")
        with self._lock:
            return _public(self._records[operation_id])

    def _stop(self) -> dict[str, Any]:
        with self._lock:
            active = self._active_operation_id
            goal_handle = None if active is None else self._goal_handles.get(active)
        if goal_handle is not None:
            goal_handle.cancel_goal_async()
        if not self._emergency_client.wait_for_service(
            timeout_sec=self._action_timeout
        ):
            raise GatewayRejected("UNAVAILABLE", "急停服务不可用")
        future = self._emergency_client.call_async(Trigger.Request())
        if not _wait_future(future, self._action_timeout):
            raise GatewayRejected("GATEWAY_TIMEOUT", "急停服务响应超时")
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            with self._lock:
                if self._active_operation_id is None:
                    break
            time.sleep(0.02)
        return self._read_status()

    def _read_status(self) -> dict[str, Any]:
        now = time.monotonic()
        odom_timeout = float(self.get_parameter("odom_timeout").value)
        with self._lock:
            age = (
                None
                if self._last_odom_at is None
                else max(0.0, now - self._last_odom_at)
            )
            active = self._active_operation_id
            last = next(reversed(self._records.values()), None) if self._records else None
            return {
                "online": age is not None and age <= odom_timeout,
                "odom_age_seconds": age,
                "pose": None if self._last_odom is None else dict(self._last_odom["pose"]),
                "velocity": None
                if self._last_odom is None
                else dict(self._last_odom["velocity"]),
                "gateway_status": "IDLE" if active is None else "RUNNING",
                "operation_id": active,
                "active_task_kind": self._active_kind,
                "last_operation": None if last is None else _public(last),
            }

    def _on_odom(self, message: Odometry) -> None:
        orientation = message.pose.pose.orientation
        yaw = math.atan2(
            2.0 * (orientation.w * orientation.z + orientation.x * orientation.y),
            1.0 - 2.0 * (orientation.y * orientation.y + orientation.z * orientation.z),
        )
        with self._lock:
            self._last_odom = {
                "pose": {
                    "x": float(message.pose.pose.position.x),
                    "y": float(message.pose.pose.position.y),
                    "yaw": yaw,
                },
                "velocity": {
                    "linear_x": float(message.twist.twist.linear.x),
                    "angular_z": float(message.twist.twist.angular.z),
                },
            }
            self._last_odom_at = time.monotonic()

    def _on_camera_frame(self, message: CompressedImage) -> None:
        """缓存相机最新压缩帧，供 /v1/camera/snapshot 落盘。"""
        self._snapshot_store.store(bytes(message.data), str(message.format))

    def _on_detections(self, message: Detections) -> None:
        """解析并缓存最新一帧 YOLO 检测结果。"""
        width = int(message.image_width)
        height = int(message.image_height)
        items = []
        for item in message.detections:
            x1, x2 = float(item.x1), float(item.x2)
            items.append(
                {
                    "label": str(item.label),
                    "score": round(float(item.score), 4),
                    "position": box_position(x1, x2, width),
                    "x1": round(x1, 1),
                    "y1": round(float(item.y1), 1),
                    "x2": round(x2, 1),
                    "y2": round(float(item.y2), 1),
                }
            )
        self._detection_cache.store(items, width, height)

    def _read_detections(self) -> dict[str, Any]:
        """返回新鲜检测快照；过期时按需临时拉起 YOLO 再等待。"""
        fresh = self._detection_cache.fresh()
        if fresh is not None:
            return fresh
        camera_age = self._snapshot_store.frame_age()
        if camera_age is None or camera_age > float(
            self.get_parameter("snapshot_max_age").value
        ):
            return {"status": NO_FRAME, "detections": []}
        if not self._set_perception(True, self._detections_probe_runtime):
            raise GatewayRejected("UNAVAILABLE", "感知服务不可用，无法启动 YOLO")
        deadline = time.monotonic() + self._detections_probe_timeout
        while time.monotonic() < deadline:
            fresh = self._detection_cache.fresh()
            if fresh is not None:
                return fresh
            time.sleep(0.1)
        return {"status": TIMEOUT, "detections": []}

    def _set_perception(self, enabled: bool, runtime: float) -> bool:
        """同步调用感知管理器启停 YOLO；成功返回 True。"""
        if not self._perception_client.wait_for_service(timeout_sec=2.0):
            return False
        request = SetPerception.Request()
        request.enabled = enabled
        request.max_runtime_seconds = float(runtime)
        future = self._perception_client.call_async(request)
        if not _wait_future(future, self._action_timeout):
            return False
        try:
            response = future.result()
        except Exception:  # noqa: BLE001
            return False
        return bool(response.success)

    def _validate_follow(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            operation_id = str(payload["operation_id"]).strip()
            target_label = str(payload["target_label"]).strip().lower()
            timeout = float(
                payload.get("timeout_seconds", self._follow_default_timeout)
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GatewayRejected(
                "INVALID_REQUEST", "跟随任务字段无效"
            ) from error
        if not operation_id or not target_label:
            raise GatewayRejected(
                "INVALID_REQUEST", "operation_id 和 target_label 不能为空"
            )
        if target_label not in self._supported_targets:
            raise GatewayRejected(
                "UNSUPPORTED_TARGET", f"当前 YOLO 模型不支持类别 {target_label!r}"
            )
        if not 0.0 < timeout <= self._follow_max_timeout:
            raise GatewayRejected(
                "OUT_OF_RANGE",
                f"timeout_seconds 必须大于 0 且不超过 {self._follow_max_timeout:g}",
            )
        return {
            "operation_id": operation_id,
            "target_label": target_label,
            "timeout_seconds": timeout,
        }

    def _trim_history_locked(self) -> None:
        while len(self._records) > self._history_size:
            oldest = next(iter(self._records))
            if oldest == self._active_operation_id:
                break
            self._records.pop(oldest, None)

    def close(self) -> None:
        """停止 HTTP Server；底盘急停由控制节点负责。"""
        self._http.close()


def _validate_motion(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        operation_id = str(payload["operation_id"]).strip()
        motion_type = str(payload["type"])
        mode = str(payload["mode"])
        value = float(payload["value"])
    except (KeyError, TypeError, ValueError) as error:
        raise GatewayRejected("INVALID_REQUEST", "动作字段无效") from error
    if not operation_id or not math.isfinite(value):
        raise GatewayRejected("INVALID_REQUEST", "operation_id 或 value 无效")
    linear = motion_type in {"forward", "backward"}
    angular = motion_type in {"turn_left", "turn_right"}
    if not linear and not angular:
        raise GatewayRejected("INVALID_REQUEST", "未知移动类型")
    if (linear and mode not in {"distance", "time"}) or (
        angular and mode not in {"angle", "time"}
    ):
        raise GatewayRejected("INVALID_MODE", "移动类型与完成模式不匹配")
    limits = {
        "distance": (0.05, 3.0),
        "angle": (1.0, 180.0),
        "time": (0.1, 10.0),
    }
    minimum, maximum = limits[mode]
    if not minimum <= value <= maximum:
        raise GatewayRejected("OUT_OF_RANGE", "动作目标超出允许范围")
    return {
        "operation_id": operation_id,
        "type": motion_type,
        "mode": mode,
        "value": value,
    }


def _video_qos() -> QoSProfile:
    """与相机发布端一致的 best-effort 视频 QoS。"""
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=2,
        reliability=ReliabilityPolicy.BEST_EFFORT,
    )


def _wait_future(future: Any, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while not future.done() and time.monotonic() < deadline:
        time.sleep(0.01)
    return future.done()


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def main(args: list[str] | None = None) -> None:
    """运行 HTTP/Action Gateway。"""
    rclpy.init(args=args)
    node = AgentGatewayNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except (ExternalShutdownException, KeyboardInterrupt):
        pass
    finally:
        try:
            node.close()
        except KeyboardInterrupt:
            pass
        try:
            executor.shutdown(timeout_sec=1.0)
        except KeyboardInterrupt:
            pass
        try:
            node.destroy_node()
        except KeyboardInterrupt:
            pass
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

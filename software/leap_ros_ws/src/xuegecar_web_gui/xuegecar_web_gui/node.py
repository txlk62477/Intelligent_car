"""xuegecar_web_gui 后端 ROS 节点。

一个进程同时承担三件事：
1. ROS：订阅摄像头/里程计/电池话题，发布 /cmd_vel_teleop；
2. 安全：命令看门狗（超时自动零速）、"零速连发->静默"释放仲裁、急停锁服务调用；
3. Web：内嵌 FastAPI 应用（静态页面、MJPEG 摄像头流、控制 WebSocket）。

发布状态机（关键设计）：
- ACTIVE    ：收到非零命令，按 publish_rate_hz 持续发布 Twist；
- STOPPING  ：收到停止/看门狗触发，连发 N 帧零速（保证底盘收到停车指令）；
- IDLE     ：静默不发布。twist_mux 的 manual 输入 0.5s 后过期，自动把仲裁权
              交还导航/Agent 等低优先级来源；ESP32 端保留的是最后的零速指令。
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from typing import Any

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy
from rclpy.qos import HistoryPolicy
from rclpy.qos import QoSProfile
from rclpy.qos import ReliabilityPolicy
from sensor_msgs.msg import BatteryState
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Float32
from std_srvs.srv import SetBool
from std_srvs.srv import Trigger

_STOP_BURST_TICKS = 5  # 零速连发帧数（10Hz 下约 0.5s，与 twist_mux manual 超时对齐）


class WebGuiNode(Node):
    """遥控命令入口 + 状态出口 + Web 会话管理。"""

    def __init__(self) -> None:
        super().__init__("xuegecar_web_gui")

        # ---- 参数 ----
        self.declare_parameter("host", "0.0.0.0")
        self.declare_parameter("port", 8000)
        self.declare_parameter("camera_topic", "/camera/image_raw/compressed")
        self.declare_parameter("odom_topic", "/odometry/filtered")
        self.declare_parameter("battery_state_topic", "/battery_state")
        self.declare_parameter("voltage_topics", ["/voltage", "/battery_voltage"])
        self.declare_parameter("percent_topics", ["/battery_percent"])
        self.declare_parameter("cmd_vel_topic", "/cmd_vel_teleop")
        self.declare_parameter("publish_rate_hz", 10.0)
        self.declare_parameter("command_timeout", 0.3)
        self.declare_parameter("session_timeout", 10.0)
        self.declare_parameter("max_linear_cap", 2.0)
        self.declare_parameter("max_angular_cap", 5.0)
        self.declare_parameter("default_max_linear", 0.3)
        self.declare_parameter("default_max_angular", 1.0)
        self.declare_parameter("estop_service", "/motion/emergency_stop")
        self.declare_parameter("unlock_service", "/motion/set_emergency_lock")

        self.host = str(self.get_parameter("host").value)
        self.port = int(self.get_parameter("port").value)
        self.publish_rate_hz = float(self.get_parameter("publish_rate_hz").value)
        self.command_timeout = float(self.get_parameter("command_timeout").value)
        self.session_timeout = float(self.get_parameter("session_timeout").value)
        self.max_linear_cap = float(self.get_parameter("max_linear_cap").value)
        self.max_angular_cap = float(self.get_parameter("max_angular_cap").value)
        self.estop_service_name = str(self.get_parameter("estop_service").value)
        self.unlock_service_name = str(self.get_parameter("unlock_service").value)

        # ---- 状态（跨线程共享，统一用 _lock 保护）----
        self._lock = threading.Lock()
        self._cmd_linear = 0.0
        self._cmd_angular = 0.0
        self._last_cmd_mono = 0.0
        self._state = "IDLE"          # ACTIVE | STOPPING | IDLE
        self._stop_ticks = 0
        self._max_linear = float(self.get_parameter("default_max_linear").value)
        self._max_angular = float(self.get_parameter("default_max_angular").value)
        self._odom_linear = 0.0
        self._odom_angular = 0.0
        self._battery_voltage: float | None = None
        self._battery_percent: float | None = None
        self._latest_frame: bytes | None = None
        self._frame_seq = 0
        self._frame_times: list[float] = []
        self._frame_event = threading.Event()
        self._estop_locked = False
        self._actions: list[dict[str, Any]] = []
        self._session: dict[str, Any] = {
            "active": False,
            "token": "",
            "owner_ip": "",
            "last_activity": 0.0,
        }

        # ---- ROS 发布 ----
        self._cmd_pub = self.create_publisher(
            Twist, str(self.get_parameter("cmd_vel_topic").value), 10
        )
        # ---- ROS 订阅 ----
        sensor_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self._camera_sub = self.create_subscription(
            CompressedImage,
            str(self.get_parameter("camera_topic").value),
            self._on_camera,
            sensor_qos,
        )
        self._odom_sub = self.create_subscription(
            Odometry,
            str(self.get_parameter("odom_topic").value),
            self._on_odom,
            sensor_qos,
        )
        self._battery_sub = self.create_subscription(
            BatteryState,
            str(self.get_parameter("battery_state_topic").value),
            self._on_battery_state,
            sensor_qos,
        )
        for topic in self.get_parameter("voltage_topics").value:
            self.create_subscription(
                Float32, str(topic), self._on_voltage, sensor_qos
            )
        for topic in self.get_parameter("percent_topics").value:
            self.create_subscription(
                Float32, str(topic), self._on_percent, sensor_qos
            )

        # ---- 服务客户端（急停锁/解锁）----
        self._estop_client = self.create_client(Trigger, self.estop_service_name)
        self._unlock_client = self.create_client(SetBool, self.unlock_service_name)

        # ---- 控制定时器（发布状态机 + 看门狗 + 动作队列）----
        self._period = 1.0 / max(1.0, self.publish_rate_hz)
        self._timer = self.create_timer(self._period, self._control_tick)

        self.get_logger().info(
            f"web_gui ready: http://{self.host}:{self.port} "
            f"cmd_vel={self.get_parameter('cmd_vel_topic').value}"
        )

    # ------------------------------------------------------------ 会话管理

    def try_acquire_session(self, owner_ip: str) -> tuple[str | None, str | None]:
        """返回 (token, None) 表示获得控制权；(None, owner_ip) 表示被占用。"""
        with self._lock:
            session = self._session
            if session["active"]:
                now = time.monotonic()
                if now - session["last_activity"] > self.session_timeout:
                    session["active"] = False
                else:
                    return None, session["owner_ip"]
            token = uuid.uuid4().hex
            session.update(
                {
                    "active": True,
                    "token": token,
                    "owner_ip": owner_ip,
                    "last_activity": time.monotonic(),
                }
            )
            return token, None

    def release_session(self, token: str) -> None:
        with self._lock:
            if self._session["active"] and self._session["token"] == token:
                self._session["active"] = False
                self._session["token"] = ""
                self._session["owner_ip"] = ""
                self._begin_stop_locked()

    def touch_session(self, token: str) -> bool:
        with self._lock:
            if not self._session["active"] or self._session["token"] != token:
                return False
            self._session["last_activity"] = time.monotonic()
            return True

    def session_expired(self, token: str) -> bool:
        with self._lock:
            if not self._session["active"] or self._session["token"] != token:
                return True
            return time.monotonic() - self._session["last_activity"] > self.session_timeout

    def token_valid(self, token: str) -> bool:
        with self._lock:
            return self._session["active"] and self._session["token"] == token

    # ------------------------------------------------------------ 控制入口（Web 线程调用）

    def set_command(self, linear: float, angular: float) -> None:
        linear = self._clamp(linear, self.max_linear_cap)
        angular = self._clamp(angular, self.max_angular_cap)
        with self._lock:
            self._cmd_linear = linear
            self._cmd_angular = angular
            self._last_cmd_mono = time.monotonic()
            if linear != 0.0 or angular != 0.0:
                # 非零命令：立即（重新）进入发布状态。
                self._state = "ACTIVE"
                self._stop_ticks = 0
            else:
                # 零速命令（摇杆回中/按键全松开）：进入停车连发。
                self._state = "STOPPING"
                self._stop_ticks = _STOP_BURST_TICKS

    def request_stop(self) -> None:
        with self._lock:
            self._begin_stop_locked()

    def set_speed_limits(self, max_linear: float, max_angular: float) -> None:
        with self._lock:
            self._max_linear = max(0.0, min(float(max_linear), self.max_linear_cap))
            self._max_angular = max(0.0, min(float(max_angular), self.max_angular_cap))

    def enqueue_estop(self) -> None:
        with self._lock:
            self._actions.append({"type": "estop"})

    def enqueue_unlock(self) -> None:
        with self._lock:
            self._actions.append({"type": "unlock"})

    def _begin_stop_locked(self) -> None:
        """调用方需持有 _lock。零速 + 进入停车连发。"""
        self._cmd_linear = 0.0
        self._cmd_angular = 0.0
        self._state = "STOPPING"
        self._stop_ticks = _STOP_BURST_TICKS
        self._last_cmd_mono = time.monotonic()

    # ------------------------------------------------------------ 状态快照（Web 线程调用）

    def state_snapshot(self) -> dict[str, Any]:
        with self._lock:
            if self._frame_times:
                now = time.monotonic()
                recent = [t for t in self._frame_times if now - t <= 2.0]
                self._frame_times = recent
                fps = len(recent) / 2.0 if recent else 0.0
                camera_age = now - recent[-1] if recent else -1.0
            else:
                fps = 0.0
                camera_age = -1.0
            return {
                "type": "state",
                "cmd_linear": self._cmd_linear,
                "cmd_angular": self._cmd_angular,
                "odom_linear": self._odom_linear,
                "odom_angular": self._odom_angular,
                "battery_voltage": self._battery_voltage,
                "battery_percent": self._battery_percent,
                "camera_age": camera_age,
                "camera_fps": fps,
                "max_linear": self._max_linear,
                "max_angular": self._max_angular,
                "estop_locked": self._estop_locked,
                "owner_ip": self._session["owner_ip"],
            }

    # ------------------------------------------------------------ 摄像头帧

    def wait_frame(self, last_seq: int, timeout: float) -> tuple[bytes, int] | None:
        """阻塞等待新帧；返回 (jpeg_bytes, seq) 或 None。"""
        self._frame_event.wait(timeout)
        self._frame_event.clear()
        with self._lock:
            if self._latest_frame is None or self._frame_seq == last_seq:
                return None
            return self._latest_frame, self._frame_seq

    # ------------------------------------------------------------ ROS 回调

    def _on_camera(self, msg: CompressedImage) -> None:
        if not msg.data:
            return
        with self._lock:
            self._latest_frame = bytes(msg.data)
            self._frame_seq += 1
            now = time.monotonic()
            self._frame_times.append(now)
            if len(self._frame_times) > 300:
                self._frame_times = self._frame_times[-300:]
        self._frame_event.set()

    def _on_odom(self, msg: Odometry) -> None:
        with self._lock:
            self._odom_linear = float(msg.twist.twist.linear.x)
            self._odom_angular = float(msg.twist.twist.angular.z)

    def _on_battery_state(self, msg: BatteryState) -> None:
        with self._lock:
            if math.isfinite(msg.voltage):
                self._battery_voltage = float(msg.voltage)
            if math.isfinite(msg.percentage) and msg.percentage >= 0.0:
                percent = float(msg.percentage)
                self._battery_percent = percent if percent > 1.0 else percent * 100.0

    def _on_voltage(self, msg: Float32) -> None:
        with self._lock:
            self._battery_voltage = float(msg.data)

    def _on_percent(self, msg: Float32) -> None:
        with self._lock:
            value = float(msg.data)
            self._battery_percent = value if value > 1.0 else value * 100.0

    # ------------------------------------------------------------ 控制循环（ROS 线程）

    def _control_tick(self) -> None:
        now = time.monotonic()
        with self._lock:
            # 看门狗：ACTIVE 状态下超过 command_timeout 没有新命令 -> 停车连发。
            if self._state == "ACTIVE" and now - self._last_cmd_mono > self.command_timeout:
                self._cmd_linear = 0.0
                self._cmd_angular = 0.0
                self._state = "STOPPING"
                self._stop_ticks = _STOP_BURST_TICKS

            publish_cmd = False
            if self._state == "ACTIVE":
                publish_cmd = True
            elif self._state == "STOPPING":
                self._stop_ticks -= 1
                publish_cmd = True
                if self._stop_ticks <= 0:
                    # 零速连发完毕 -> 静默，释放 twist_mux 仲裁权。
                    self._state = "IDLE"

            linear, angular = self._cmd_linear, self._cmd_angular
            actions = self._actions
            self._actions = []

        if publish_cmd:
            msg = Twist()
            msg.linear.x = linear
            msg.angular.z = angular
            self._cmd_pub.publish(msg)

        for action in actions:
            if action["type"] == "estop":
                self._handle_estop()
            elif action["type"] == "unlock":
                self._handle_unlock()

    def _handle_estop(self) -> None:
        # 优先走 motion_controller 的服务（锁 twist_mux，全系统生效）。
        if self._call_service(self._estop_client, Trigger.Request):
            with self._lock:
                self._estop_locked = True
                self._begin_stop_locked()
            self.get_logger().info("急停锁已激活（motion_controller 服务）")
            return
        with self._lock:
            self._estop_locked = True
            self._begin_stop_locked()
        self.get_logger().error("motion_controller 急停服务不可用，仅停止 Web 手动速度")

    def _handle_unlock(self) -> None:
        if self._call_service(self._unlock_client, SetBool.Request, data=False):
            with self._lock:
                self._estop_locked = False
            self.get_logger().info("急停锁已解除（motion_controller 服务）")
            return
        with self._lock:
            self._estop_locked = False
        self.get_logger().error("motion_controller 解锁服务不可用")

    def _call_service(self, client, request_type, **kwargs) -> bool:
        if not client.wait_for_service(timeout_sec=1.0):
            return False
        request = request_type(**kwargs)
        future = client.call_async(request)
        deadline = time.monotonic() + 3.0
        while not future.done() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not future.done():
            return False
        try:
            result = future.result()
        except Exception:  # noqa: BLE001
            return False
        return bool(getattr(result, "success", True))

    @staticmethod
    def _clamp(value: float, cap: float) -> float:
        value = float(value)
        if not math.isfinite(value):
            return 0.0
        return max(-cap, min(cap, value))

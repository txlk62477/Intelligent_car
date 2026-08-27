"""同时承载 ROS2 控制循环与本机 HTTP Adapter 的 Gateway 节点。"""

from __future__ import annotations

from dataclasses import replace
import math
from queue import Empty, Queue
from threading import Event, Lock
import time
from typing import Any

from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

from xuegecar_agent_bridge.controller import (
    ControllerConfig,
    MotionCommand,
    MotionController,
    MotionRejected,
    OdomSnapshot,
)
from xuegecar_agent_bridge.http_server import GatewayHttpServer


class _Request:
    """跨越 HTTP 线程与 ROS 主线程的一次同步请求。"""

    def __init__(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        # 请求类型：submit 表示提交动作，stop 表示停止当前动作。
        self.kind = kind
        # HTTP JSON 请求体；stop 没有参数时使用空字典。
        self.payload = payload or {}
        # 跨线程完成信号：HTTP 线程 wait()，ROS 主线程处理后 set()。
        # done 只表示处理结束，不保存结果，也不具备取消队列请求的能力。
        self.done = Event()
        # ROS 主线程处理成功后写入的 JSON 可序列化结果。
        self.result: dict[str, Any] | None = None
        # ROS 主线程处理失败后写入的异常，由 HTTP 线程重新抛出并映射为错误响应。
        self.error: BaseException | None = None


class AgentGatewayNode(Node):
    """让 ROS 主线程独占控制器，并向 HTTP 线程提供快照。"""

    def __init__(self) -> None:
        super().__init__("xuegecar_agent_bridge")
        defaults = ControllerConfig()

        # ROS 参数既提供代码默认值，也可由 gateway.yaml 在启动时覆盖。
        self.declare_parameter("http_host", "127.0.0.1")
        self.declare_parameter("http_port", 8765)
        self.declare_parameter("odom_topic", "/odom")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("control_rate_hz", 20.0)
        for name in (
            "linear_speed",
            "angular_speed",
            "min_distance",
            "max_distance",
            "min_duration",
            "max_duration",
            "min_angle_degrees",
            "max_angle_degrees",
            "distance_tolerance",
            "angle_tolerance_degrees",
            "odom_timeout",
            "action_timeout",
            "slow_down_ratio",
        ):
            self.declare_parameter(name, getattr(defaults, name))
        self.declare_parameter("stop_publish_count", defaults.stop_publish_count)
        self.declare_parameter("history_size", defaults.history_size)

        config = replace(
            defaults,
            **{
                name: self.get_parameter(name).value
                for name in defaults.__dataclass_fields__
            },
        )
        # 控制器不依赖 rclpy；节点负责把 ROS 消息转换成它的输入和输出。
        self._controller = MotionController(config)

        # HTTP 线程只把写请求放进 Queue，控制器始终由 ROS 主线程独占。
        self._requests: Queue[_Request] = Queue()
        # 查询接口无需进入请求队列，只读取加锁后的不可变状态副本。
        self._snapshot_lock = Lock()
        self._status_snapshot = self._controller.status(time.monotonic())
        self._operation_snapshots: dict[str, dict[str, Any]] = {}

        cmd_vel_topic = str(self.get_parameter("cmd_vel_topic").value)
        odom_topic = str(self.get_parameter("odom_topic").value)
        # /cmd_vel 是控制输出，/odom 是判断距离、角度和在线状态的反馈。
        self._publisher = self.create_publisher(Twist, cmd_vel_topic, 10)
        self._subscription = self.create_subscription(
            Odometry,
            odom_topic,
            self._on_odom,
            qos_profile_sensor_data,
        )
        rate = float(self.get_parameter("control_rate_hz").value)
        if rate <= 0.0:
            raise ValueError("control_rate_hz 必须大于 0")
        # Timer 在 ROS executor 中运行：20 Hz 时每 50 ms 推进一次控制状态机。
        self._timer = self.create_timer(1.0 / rate, self._tick)

        host = str(self.get_parameter("http_host").value)
        port = int(self.get_parameter("http_port").value)
        # HTTP Server 只认识回调接口；submit/stop 最终会转入上面的请求队列。
        self._http = GatewayHttpServer(
            host,
            port,
            status=self._read_status,
            operation=self._read_operation,
            submit=self._submit_from_http,
            stop=self._stop_from_http,
        )
        self._http.start()
        self.get_logger().info(
            f"Robot Gateway listening on http://{host}:{port}; odom={odom_topic}, cmd_vel={cmd_vel_topic}"
        )

    def close(self) -> None:
        """正常关闭时先停止 HTTP，再尽力发送多次零速度。"""

        # 先拒绝新请求，再取消当前动作，最后直接发布冗余的零速度消息。
        self._http.close()
        self._controller.stop(time.monotonic(), "Gateway 正常关闭")
        for _ in range(self._controller.config.stop_publish_count):
            self._publish_velocity(0.0, 0.0)

    def _on_odom(self, message: Odometry) -> None:
        orientation = message.pose.pose.orientation
        # 平面小车只关心绕 Z 轴的 yaw，这里直接从四元数中提取。
        yaw = math.atan2(
            2.0 * (orientation.w * orientation.z + orientation.x * orientation.y),
            1.0 - 2.0 * (orientation.y * orientation.y + orientation.z * orientation.z),
        )
        self._controller.update_odom(
            OdomSnapshot(
                x=float(message.pose.pose.position.x),
                y=float(message.pose.pose.position.y),
                yaw=yaw,
                linear_x=float(message.twist.twist.linear.x),
                angular_z=float(message.twist.twist.angular.z),
                received_at=time.monotonic(),
            )
        )

    def _tick(self) -> None:
        now = time.monotonic()
        # 固定顺序：接收新命令 → 推进控制器 → 发布速度 → 更新查询快照。
        self._drain_requests(now)
        velocity = self._controller.tick(now)
        if velocity is not None:
            self._publish_velocity(velocity.linear_x, velocity.angular_z)
        self._refresh_snapshots(now)

    def _drain_requests(self, now: float) -> None:
        # 该函数只从 ROS Timer 回调调用，因此控制器不会被 HTTP 线程并发修改。
        while True:
            try:
                request = self._requests.get_nowait()
            except Empty:
                return
            try:
                if request.kind == "submit":
                    # 在 ROS 主线程内完成外部 JSON 的最终校验和动作启动。
                    command = MotionCommand.from_mapping(request.payload)
                    request.result = self._controller.submit(command, now)
                elif request.kind == "stop":
                    request.result = self._controller.stop(now)
                else:
                    raise RuntimeError(f"未知请求类型：{request.kind}")
            except BaseException as error:
                request.error = error
            finally:
                self._refresh_snapshots(now)
                # 无论成功还是异常，都必须唤醒正在等待 HTTP 响应的线程。
                # 若 HTTP 已超时退出，set() 仍可安全调用，但此时不会再次产生 HTTP 回复。
                request.done.set()

    def _request(self, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = _Request(kind, payload)
        # queue.Queue 的 put/get 是线程安全的，这是 HTTP → ROS 的写入桥梁。
        self._requests.put(request)
        # HTTP 请求保持同步语义，但不会无限等待失去响应的 ROS executor。
        # 注意：等待超时只结束 HTTP 请求，不会从队列移除或取消该 ROS 请求；
        # ROS 主线程恢复后仍可能处理它，并在没有等待者时调用 done.set()。
        if not request.done.wait(timeout=2.0):
            raise TimeoutError("ROS2 主线程未在 2 秒内响应")
        if request.error is not None:
            raise request.error
        assert request.result is not None
        return request.result

    def _submit_from_http(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("submit", payload)

    def _stop_from_http(self) -> dict[str, Any]:
        return self._request("stop")

    def _read_status(self) -> dict[str, Any]:
        # 返回副本，避免 HTTP Handler 修改节点内部保存的快照。
        with self._snapshot_lock:
            return dict(self._status_snapshot)

    def _read_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self._snapshot_lock:
            value = self._operation_snapshots.get(operation_id)
            return None if value is None else dict(value)

    def _refresh_snapshots(self, now: float) -> None:
        status = self._controller.status(now)
        operation = status.get("last_operation")
        # 锁只保护快速的内存替换，不在持锁期间执行控制计算或网络操作。
        with self._snapshot_lock:
            self._status_snapshot = status
            if isinstance(operation, dict):
                self._operation_snapshots[str(operation["operation_id"])] = operation
                while len(self._operation_snapshots) > self._controller.config.history_size:
                    self._operation_snapshots.pop(next(iter(self._operation_snapshots)))

    def _publish_velocity(self, linear_x: float, angular_z: float) -> None:
        # 控制器输出与 ROS2 geometry_msgs/Twist 的适配点。
        message = Twist()
        message.linear.x = linear_x
        message.angular.z = angular_z
        self._publisher.publish(message)


def main(args: list[str] | None = None) -> None:
    """运行 Robot Gateway。"""

    rclpy.init(args=args)
    node: AgentGatewayNode | None = None
    try:
        node = AgentGatewayNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.close()
            node.destroy_node()
        rclpy.shutdown()

"""按任务启动和关闭 YOLO launch 子进程的轻量 ROS2 节点。"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from threading import Lock

import rclpy
from leap_interfaces.srv import SetPerception
from rclpy.node import Node


class PerceptionManagerNode(Node):
    """通过小接口隐藏 YOLO 子进程生命周期细节。"""

    def __init__(self) -> None:
        super().__init__("perception_manager")
        self.declare_parameter("launch_package", "xuegecar_perception")
        self.declare_parameter("launch_file", "perception.launch.py")
        self.declare_parameter("max_runtime_seconds", 300.0)
        self.declare_parameter("stop_timeout_seconds", 5.0)
        self._lock = Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._deadline: float | None = None
        self.create_service(
            SetPerception,
            "/perception/set_enabled",
            self._set_enabled,
        )
        self.create_timer(0.5, self._watch_process)
        self.get_logger().info("Perception Manager ready; YOLO is stopped")

    def _set_enabled(
        self,
        request: SetPerception.Request,
        response: SetPerception.Response,
    ) -> SetPerception.Response:
        with self._lock:
            self._refresh_locked()
            if request.enabled:
                self._start_locked(float(request.max_runtime_seconds), response)
            else:
                self._stop_locked()
                response.success = True
                response.state = "STOPPED"
        return response

    def _start_locked(
        self, requested_runtime: float, response: SetPerception.Response
    ) -> None:
        maximum = float(self.get_parameter("max_runtime_seconds").value)
        runtime = requested_runtime if requested_runtime > 0.0 else maximum
        if runtime > maximum:
            response.success = False
            response.state = "STOPPED"
            response.error_code = "OUT_OF_RANGE"
            response.error = f"YOLO 最大运行时间为 {maximum:g} 秒"
            return
        if self._process is not None:
            response.success = True
            response.state = "RUNNING"
            return
        ros2 = shutil.which("ros2")
        if ros2 is None:
            response.success = False
            response.state = "STOPPED"
            response.error_code = "ROS2_NOT_FOUND"
            response.error = "找不到 ros2 命令"
            return
        package = str(self.get_parameter("launch_package").value)
        launch_file = str(self.get_parameter("launch_file").value)
        try:
            self._process = subprocess.Popen(
                [ros2, "launch", package, launch_file],
                start_new_session=True,
            )
        except OSError as error:
            response.success = False
            response.state = "STOPPED"
            response.error_code = "START_FAILED"
            response.error = f"无法启动 YOLO：{error}"
            return
        self._deadline = time.monotonic() + runtime
        response.success = True
        response.state = "STARTING"
        self.get_logger().info(
            f"Started YOLO process pid={self._process.pid}, max_runtime={runtime:g}s"
        )

    def _watch_process(self) -> None:
        with self._lock:
            self._refresh_locked()
            if (
                self._process is not None
                and self._deadline is not None
                and time.monotonic() >= self._deadline
            ):
                self.get_logger().warning("YOLO reached its maximum runtime; stopping")
                self._stop_locked()

    def _refresh_locked(self) -> None:
        if self._process is not None and self._process.poll() is not None:
            return_code = self._process.returncode
            self.get_logger().warning(f"YOLO process exited with code {return_code}")
            self._process = None
            self._deadline = None

    def _stop_locked(self) -> None:
        process = self._process
        self._process = None
        self._deadline = None
        if process is None or process.poll() is not None:
            return
        timeout = float(self.get_parameter("stop_timeout_seconds").value)
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGINT)
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait(timeout=2.0)
        except ProcessLookupError:
            pass
        self.get_logger().info("YOLO process stopped")

    def destroy_node(self) -> None:
        with self._lock:
            self._stop_locked()
        super().destroy_node()


def main(args: list[str] | None = None) -> None:
    """运行 Perception Manager。"""
    rclpy.init(args=args)
    node = PerceptionManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node.destroy_node()
        except KeyboardInterrupt:
            pass
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

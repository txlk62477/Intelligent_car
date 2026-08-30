"""ROS2 Action Server：独占底盘速度并执行移动与视觉跟随任务。"""

from __future__ import annotations

import math
import time
from dataclasses import replace
from threading import Lock
from typing import Any

import rclpy
from geometry_msgs.msg import Twist
from leap_interfaces.action import ExecuteMotion, FollowTarget
from leap_interfaces.msg import Detections
from leap_interfaces.srv import SetPerception
from nav_msgs.msg import Odometry
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from std_srvs.srv import Trigger

from xuegecar_motion_controller.controller import (
    ControllerConfig,
    MotionCommand,
    MotionController,
    MotionRejected,
    MotionStatus,
    OdomSnapshot,
)
from xuegecar_motion_controller.visual_controller import (
    TERMINAL_FOLLOW_STATUSES,
    DetectionBox,
    FollowConfig,
    VisualFollowController,
)

MOTION_TERMINAL = {
    MotionStatus.SUCCEEDED.value,
    MotionStatus.FAILED.value,
    MotionStatus.TIMED_OUT.value,
    MotionStatus.CANCELLED.value,
    MotionStatus.ODOM_TIMEOUT.value,
}


class MotionControllerNode(Node):
    """唯一发布 `/cmd_vel` 的任务执行节点。"""

    def __init__(self) -> None:
        super().__init__("xuegecar_motion_controller")
        self._callbacks = ReentrantCallbackGroup()
        self._lock = Lock()
        self._reserved = False
        self._active_kind: str | None = None
        self._emergency_epoch = 0
        self._follow: VisualFollowController | None = None

        controller_defaults = ControllerConfig()
        follow_defaults = FollowConfig()
        self._declare_parameters(controller_defaults, follow_defaults)
        controller_config = replace(
            controller_defaults,
            **{
                name: self.get_parameter(name).value
                for name in controller_defaults.__dataclass_fields__
            },
        )
        self._controller = MotionController(controller_config)
        self._follow_config = FollowConfig(
            startup_timeout=float(self.get_parameter("follow_startup_timeout").value),
            search_timeout=float(self.get_parameter("follow_search_timeout").value),
            detection_timeout=float(self.get_parameter("detection_timeout").value),
            min_confidence=float(
                self.get_parameter("min_detection_confidence").value
            ),
            stable_frames=int(self.get_parameter("stable_frames").value),
            center_deadzone=float(self.get_parameter("center_deadzone").value),
            area_deadzone=float(self.get_parameter("area_deadzone").value),
            max_linear_speed=float(
                self.get_parameter("follow_max_linear_speed").value
            ),
            max_angular_speed=float(
                self.get_parameter("follow_max_angular_speed").value
            ),
            linear_kp=float(self.get_parameter("follow_linear_kp").value),
            angular_kp=float(self.get_parameter("follow_angular_kp").value),
            min_linear_speed=float(
                self.get_parameter("follow_min_linear_speed").value
            ),
            min_angular_speed=float(
                self.get_parameter("follow_min_angular_speed").value
            ),
        )
        self._default_follow_timeout = float(
            self.get_parameter("follow_default_timeout").value
        )
        self._max_follow_timeout = float(
            self.get_parameter("follow_max_timeout").value
        )
        self._period = 1.0 / float(self.get_parameter("control_rate_hz").value)

        cmd_vel_topic = str(self.get_parameter("cmd_vel_topic").value)
        odom_topic = str(self.get_parameter("odom_topic").value)
        detections_topic = str(self.get_parameter("detections_topic").value)
        self._publisher = self.create_publisher(Twist, cmd_vel_topic, 10)
        self.create_subscription(
            Odometry,
            odom_topic,
            self._on_odom,
            qos_profile_sensor_data,
            callback_group=self._callbacks,
        )
        self.create_subscription(
            Detections,
            detections_topic,
            self._on_detections,
            qos_profile_sensor_data,
            callback_group=self._callbacks,
        )
        self._perception = self.create_client(
            SetPerception,
            "/perception/set_enabled",
            callback_group=self._callbacks,
        )
        self._emergency_service = self.create_service(
            Trigger,
            "/motion/emergency_stop",
            self._on_emergency_stop,
            callback_group=self._callbacks,
        )
        self._motion_action = ActionServer(
            self,
            ExecuteMotion,
            "/motion/execute",
            execute_callback=self._execute_motion,
            goal_callback=self._accept_motion,
            cancel_callback=self._accept_cancel,
            callback_group=self._callbacks,
        )
        self._follow_action = ActionServer(
            self,
            FollowTarget,
            "/motion/follow_target",
            execute_callback=self._execute_follow,
            goal_callback=self._accept_follow,
            cancel_callback=self._accept_cancel,
            callback_group=self._callbacks,
        )
        self.get_logger().info(
            f"Motion Controller ready; odom={odom_topic}, detections={detections_topic}, "
            f"exclusive cmd_vel={cmd_vel_topic}"
        )

    def _declare_parameters(
        self, controller: ControllerConfig, follow: FollowConfig
    ) -> None:
        self.declare_parameter("odom_topic", "/odometry/filtered")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("detections_topic", "/vision/detections")
        self.declare_parameter("control_rate_hz", 20.0)
        for name in controller.__dataclass_fields__:
            self.declare_parameter(name, getattr(controller, name))
        self.declare_parameter("follow_default_timeout", 60.0)
        self.declare_parameter("follow_max_timeout", 300.0)
        self.declare_parameter("follow_startup_timeout", follow.startup_timeout)
        self.declare_parameter("follow_search_timeout", follow.search_timeout)
        self.declare_parameter("detection_timeout", follow.detection_timeout)
        self.declare_parameter("min_detection_confidence", follow.min_confidence)
        self.declare_parameter("stable_frames", follow.stable_frames)
        self.declare_parameter("center_deadzone", follow.center_deadzone)
        self.declare_parameter("area_deadzone", follow.area_deadzone)
        self.declare_parameter("follow_max_linear_speed", follow.max_linear_speed)
        self.declare_parameter("follow_max_angular_speed", follow.max_angular_speed)
        self.declare_parameter("follow_linear_kp", follow.linear_kp)
        self.declare_parameter("follow_angular_kp", follow.angular_kp)
        self.declare_parameter("follow_min_linear_speed", follow.min_linear_speed)
        self.declare_parameter("follow_min_angular_speed", follow.min_angular_speed)

    def _accept_motion(self, goal: ExecuteMotion.Goal) -> GoalResponse:
        try:
            MotionCommand.from_mapping(
                {
                    "operation_id": goal.operation_id,
                    "type": goal.type,
                    "mode": goal.mode,
                    "value": goal.value,
                }
            )
        except MotionRejected:
            return GoalResponse.REJECT
        return self._reserve_goal()

    def _accept_follow(self, goal: FollowTarget.Goal) -> GoalResponse:
        timeout = float(goal.timeout_seconds or self._default_follow_timeout)
        if not goal.operation_id.strip() or not goal.target_label.strip():
            return GoalResponse.REJECT
        if timeout <= 0.0 or timeout > self._max_follow_timeout:
            return GoalResponse.REJECT
        return self._reserve_goal()

    def _reserve_goal(self) -> GoalResponse:
        with self._lock:
            if self._reserved or self._active_kind is not None:
                return GoalResponse.REJECT
            self._reserved = True
        return GoalResponse.ACCEPT

    @staticmethod
    def _accept_cancel(_goal_handle: Any) -> CancelResponse:
        return CancelResponse.ACCEPT

    def _execute_motion(self, goal_handle: Any) -> ExecuteMotion.Result:
        goal = goal_handle.request
        with self._lock:
            self._reserved = False
            self._active_kind = "motion"
            emergency_epoch = self._emergency_epoch
        result = ExecuteMotion.Result()
        try:
            command = MotionCommand.from_mapping(
                {
                    "operation_id": goal.operation_id,
                    "type": goal.type,
                    "mode": goal.mode,
                    "value": goal.value,
                }
            )
            with self._lock:
                record = self._controller.submit(command, time.monotonic())
            while record["status"] not in MOTION_TERMINAL:
                if goal_handle.is_cancel_requested or self._emergency_changed(
                    emergency_epoch
                ):
                    with self._lock:
                        self._controller.stop(time.monotonic())
                        record = self._controller.operation(goal.operation_id)
                    break
                with self._lock:
                    velocity = self._controller.tick(time.monotonic())
                    record = self._controller.operation(goal.operation_id)
                if velocity is not None:
                    self._publish_velocity(velocity.linear_x, velocity.angular_z)
                feedback = ExecuteMotion.Feedback()
                feedback.status = str(record["status"])
                feedback.progress = float(record["progress"])
                goal_handle.publish_feedback(feedback)
                time.sleep(self._period)

            result.status = str(record["status"])
            result.progress = float(record["progress"])
            result.error_code = str(record.get("error_code") or "")
            result.error = str(record.get("error") or "")
            self._finish_goal(goal_handle, result.status)
            return result
        except MotionRejected as error:
            result.status = "FAILED"
            result.error_code = error.code
            result.error = str(error)
            goal_handle.abort()
            return result
        except Exception as error:  # noqa: BLE001
            self.get_logger().error(f"位置控制任务异常: {error}")
            result.status = "FAILED"
            result.error_code = "INTERNAL_ERROR"
            result.error = "位置控制节点发生内部错误"
            goal_handle.abort()
            return result
        finally:
            self._publish_stop_burst()
            self._release_goal()

    def _execute_follow(self, goal_handle: Any) -> FollowTarget.Result:
        goal = goal_handle.request
        timeout = float(goal.timeout_seconds or self._default_follow_timeout)
        started = time.monotonic()
        controller = VisualFollowController(
            goal.target_label,
            timeout,
            self._follow_config,
        )
        controller.start(started)
        with self._lock:
            self._reserved = False
            self._active_kind = "follow"
            self._follow = controller
            emergency_epoch = self._emergency_epoch
        result = FollowTarget.Result()
        perception_started = False
        try:
            perception_response = self._set_perception(True, timeout)
            if perception_response is None:
                controller.fail(
                    time.monotonic(),
                    "PERCEPTION_UNAVAILABLE",
                    "Perception Manager 不可用",
                )
            elif not perception_response.success:
                controller.fail(
                    time.monotonic(),
                    perception_response.error_code or "PERCEPTION_START_FAILED",
                    perception_response.error or "YOLO 启动失败",
                )
            else:
                perception_started = True

            while True:
                now = time.monotonic()
                if goal_handle.is_cancel_requested or self._emergency_changed(
                    emergency_epoch
                ):
                    controller.cancel(now)
                with self._lock:
                    snapshot = controller.tick(now)
                self._publish_velocity(snapshot.linear_x, snapshot.angular_z)
                self._publish_follow_feedback(goal_handle, snapshot)
                if snapshot.status in TERMINAL_FOLLOW_STATUSES:
                    break
                time.sleep(self._period)

            self._copy_follow_result(result, snapshot)
            self._finish_goal(goal_handle, snapshot.status)
            return result
        except Exception as error:  # noqa: BLE001
            self.get_logger().error(f"视觉跟随任务异常: {error}")
            snapshot = controller.fail(
                time.monotonic(), "INTERNAL_ERROR", "视觉跟随节点发生内部错误"
            )
            self._copy_follow_result(result, snapshot)
            goal_handle.abort()
            return result
        finally:
            if perception_started:
                self._set_perception(False, 0.0)
            self._publish_stop_burst()
            with self._lock:
                self._follow = None
            self._release_goal()

    def _on_odom(self, message: Odometry) -> None:
        orientation = message.pose.pose.orientation
        yaw = math.atan2(
            2.0 * (orientation.w * orientation.z + orientation.x * orientation.y),
            1.0 - 2.0 * (orientation.y * orientation.y + orientation.z * orientation.z),
        )
        with self._lock:
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

    def _on_detections(self, message: Detections) -> None:
        boxes = [
            DetectionBox(
                label=item.label,
                score=float(item.score),
                x1=float(item.x1),
                y1=float(item.y1),
                x2=float(item.x2),
                y2=float(item.y2),
            )
            for item in message.detections
        ]
        with self._lock:
            if self._follow is not None:
                self._follow.update(
                    boxes,
                    int(message.image_width),
                    int(message.image_height),
                    time.monotonic(),
                )

    def _on_emergency_stop(
        self, _request: Trigger.Request, response: Trigger.Response
    ) -> Trigger.Response:
        now = time.monotonic()
        with self._lock:
            self._emergency_epoch += 1
            self._controller.stop(now, "收到急停请求")
            if self._follow is not None:
                self._follow.cancel(now, "收到急停请求")
        self._publish_stop_burst()
        response.success = True
        response.message = "已取消当前任务并停车"
        return response

    def _set_perception(
        self, enabled: bool, max_runtime: float
    ) -> SetPerception.Response | None:
        if not self._perception.wait_for_service(timeout_sec=2.0):
            return None
        request = SetPerception.Request()
        request.enabled = enabled
        request.max_runtime_seconds = float(max_runtime)
        future = self._perception.call_async(request)
        deadline = time.monotonic() + 5.0
        while not future.done() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not future.done():
            return None
        try:
            return future.result()
        except Exception:  # noqa: BLE001
            return None

    def _emergency_changed(self, initial: int) -> bool:
        with self._lock:
            return self._emergency_epoch != initial

    def _release_goal(self) -> None:
        with self._lock:
            self._reserved = False
            self._active_kind = None

    @staticmethod
    def _finish_goal(goal_handle: Any, status: str) -> None:
        if status == "SUCCEEDED":
            goal_handle.succeed()
        elif status == "CANCELLED":
            goal_handle.canceled()
        else:
            goal_handle.abort()

    @staticmethod
    def _publish_follow_feedback(goal_handle: Any, snapshot: Any) -> None:
        feedback = FollowTarget.Feedback()
        feedback.status = snapshot.status
        feedback.elapsed_seconds = float(snapshot.elapsed_seconds)
        feedback.target_visible = snapshot.target_visible
        feedback.confidence = float(snapshot.confidence)
        feedback.center_error = float(snapshot.center_error)
        feedback.area_ratio = float(snapshot.area_ratio)
        feedback.linear_x = float(snapshot.linear_x)
        feedback.angular_z = float(snapshot.angular_z)
        goal_handle.publish_feedback(feedback)

    @staticmethod
    def _copy_follow_result(result: Any, snapshot: Any) -> None:
        result.status = snapshot.status
        result.elapsed_seconds = float(snapshot.elapsed_seconds)
        result.target_visible = snapshot.target_visible
        result.confidence = float(snapshot.confidence)
        result.center_error = float(snapshot.center_error)
        result.area_ratio = float(snapshot.area_ratio)
        result.linear_x = float(snapshot.linear_x)
        result.angular_z = float(snapshot.angular_z)
        result.error_code = snapshot.error_code
        result.error = snapshot.error

    def _publish_velocity(self, linear_x: float, angular_z: float) -> None:
        message = Twist()
        message.linear.x = linear_x
        message.angular.z = angular_z
        self._publisher.publish(message)

    def _publish_stop_burst(self) -> None:
        for _ in range(self._controller.config.stop_publish_count):
            self._publish_velocity(0.0, 0.0)

    def destroy_node(self) -> None:
        self._motion_action.destroy()
        self._follow_action.destroy()
        super().destroy_node()


def main(args: list[str] | None = None) -> None:
    """运行独立运动控制节点。"""
    rclpy.init(args=args)
    node = MotionControllerNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except (ExternalShutdownException, KeyboardInterrupt):
        pass
    finally:
        if rclpy.ok():
            node._publish_stop_burst()
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

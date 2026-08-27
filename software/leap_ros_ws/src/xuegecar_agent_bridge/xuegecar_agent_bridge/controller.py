"""不依赖 ROS2 的相对运动控制核心。"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
from enum import Enum
import math
from typing import Any


class MotionType(str, Enum):
    """允许执行的原子动作。"""

    FORWARD = "forward"
    BACKWARD = "backward"
    TURN_LEFT = "turn_left"
    TURN_RIGHT = "turn_right"


class MotionMode(str, Enum):
    """原子动作的终止条件。"""

    DISTANCE = "distance"
    ANGLE = "angle"
    TIME = "time"


class MotionStatus(str, Enum):
    """Gateway 对外公开的动作状态。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    ODOM_TIMEOUT = "ODOM_TIMEOUT"


TERMINAL_STATUSES = {
    # 进入这些状态后，Agent 不再轮询等待动作继续执行。
    MotionStatus.SUCCEEDED,
    MotionStatus.FAILED,
    MotionStatus.TIMED_OUT,
    MotionStatus.CANCELLED,
    MotionStatus.ODOM_TIMEOUT,
}


class MotionRejected(ValueError):
    """动作在启动前被确定性拒绝。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ControllerConfig:
    """运动限制及控制参数。"""

    # 基础速度。接近目标时会由 slow_down_ratio 按比例降低。
    linear_speed: float = 0.10
    angular_speed: float = 0.40
    # Agent 可提交的距离、持续时间和角度范围。
    min_distance: float = 0.05
    max_distance: float = 3.00
    min_duration: float = 0.10
    max_duration: float = 10.00
    min_angle_degrees: float = 1.0
    max_angle_degrees: float = 180.0
    # 闭环完成条件以及运行时安全限制。
    distance_tolerance: float = 0.03
    angle_tolerance_degrees: float = 3.0
    odom_timeout: float = 1.0
    action_timeout: float = 60.0
    slow_down_ratio: float = 0.35
    stop_publish_count: int = 5
    history_size: int = 100


@dataclass(frozen=True)
class MotionCommand:
    """经过 JSON seam 传入的单个相对运动。"""

    operation_id: str
    type: MotionType
    mode: MotionMode
    value: float

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "MotionCommand":
        """从外部字典构造严格命令。"""

        # HTTP JSON 属于不可信边界：字段缺失、枚举错误和非数值都在这里拒绝。
        try:
            operation_id = str(payload["operation_id"]).strip()
            motion_type = MotionType(str(payload["type"]))
            mode = MotionMode(str(payload["mode"]))
            value = float(payload["value"])
        except KeyError as error:
            raise MotionRejected("INVALID_REQUEST", f"缺少字段：{error.args[0]}") from error
        except (TypeError, ValueError) as error:
            raise MotionRejected("INVALID_REQUEST", "动作字段类型或枚举值无效") from error
        if not operation_id:
            raise MotionRejected("INVALID_REQUEST", "operation_id 不能为空")
        if not math.isfinite(value):
            raise MotionRejected("INVALID_REQUEST", "value 必须是有限数值")
        return cls(operation_id, motion_type, mode, value)


@dataclass(frozen=True)
class OdomSnapshot:
    """最近一次里程计快照，received_at 使用单调时钟。"""

    x: float             # 小车在里程计坐标系中的 X 位置，单位：m
    y: float             # 小车在里程计坐标系中的 Y 位置，单位：m
    yaw: float           # 小车绕 Z 轴的航向角，单位：rad
    linear_x: float      # 小车当前沿自身 X 轴的线速度，单位：m/s
    angular_z: float     # 小车当前绕 Z 轴的角速度，单位：rad/s
    received_at: float   # 收到该里程计消息的单调时钟时间，单位：s
#  linear.x > 0：向前  linear.x < 0：向后
#  angular.z > 0：左转 angular.z < 0：右转

@dataclass
class MotionRecord:
    """一个动作的可观测状态。"""

    operation_id: str                 # Agent 提供的幂等动作标识
    type: str                         # 动作类型，如 forward 或 turn_left
    mode: str                         # 完成模式：distance、angle 或 time
    value: float                      # 目标量；单位由 mode 决定
    status: MotionStatus              # 当前动作状态
    started_at: float | None = None   # 启动时的单调时钟时间；未启动为 None
    finished_at: float | None = None  # 进入终态的单调时钟时间；运行中为 None
    progress: float = 0.0             # 已完成量；单位与 value 相同
    error_code: str | None = None     # 稳定的机器可读错误码；无错误为 None
    error: str | None = None          # 面向用户的错误说明；无错误为 None

    def public(self) -> dict[str, Any]:
        """返回 JSON 可序列化副本。"""

        result = asdict(self)
        result["status"] = self.status.value
        return result


@dataclass(frozen=True)
class VelocityCommand:
    """控制核心希望 ROS2 发布的速度。"""

    linear_x: float
    angular_z: float


class MotionController:
    """在一个小接口后封装验证、闭环、超时、幂等与停车。"""

    def __init__(self, config: ControllerConfig | None = None) -> None:
        self.config = config or ControllerConfig()
        # _odom 保存最新反馈，_active 表示系统同时最多执行一个动作。
        self._odom: OdomSnapshot | None = None
        self._active: MotionRecord | None = None
        # OrderedDict 同时承担 operation_id 幂等表和有界历史记录。
        self._records: OrderedDict[str, MotionRecord] = OrderedDict()
        # 距离动作以起始位姿为参考；角度动作累计每帧 yaw 增量。
        self._start_odom: OdomSnapshot | None = None
        self._last_yaw: float | None = None
        self._accumulated_yaw = 0.0
        self._stop_ticks_remaining = 0

    def update_odom(self, odom: OdomSnapshot) -> None:
        """记录新里程计并累计跨越 ±π 的航向变化。"""

        if self._active is not None and self._last_yaw is not None:
            # 单次增量先归一化，避免 yaw 从 +π 跳到 -π 时误算约 2π。
            self._accumulated_yaw += normalize_angle(odom.yaw - self._last_yaw)
        self._last_yaw = odom.yaw
        self._odom = odom

    def submit(self, command: MotionCommand, now: float) -> dict[str, Any]:
        """幂等提交一个动作，忙碌或输入无效时拒绝。"""

        # 同一 operation_id 重试相同命令时直接返回旧记录，防止重复移动。
        previous = self._records.get(command.operation_id)
        if previous is not None:
            same_command = (
                previous.type == command.type.value
                and previous.mode == command.mode.value
                and previous.value == command.value
            )
            if not same_command:
                raise MotionRejected("ID_CONFLICT", "operation_id 已用于不同动作")
            return previous.public()
        if self._active is not None:
            raise MotionRejected("BUSY", "已有动作正在执行")
        self._validate(command)
        # 没有及时更新的里程计就无法安全进行闭环控制，因此拒绝启动。
        if self._odom is None or now - self._odom.received_at > self.config.odom_timeout:
            raise MotionRejected("ODOM_TIMEOUT", "没有新鲜的 /odom，拒绝启动")

        record = MotionRecord(
            operation_id=command.operation_id,
            type=command.type.value,
            mode=command.mode.value,
            value=command.value,
            status=MotionStatus.RUNNING,
            started_at=now,
        )
        self._active = record
        self._remember(record)
        # 保存本次动作的参考原点，并清零角度累计量。
        self._start_odom = self._odom
        self._last_yaw = self._odom.yaw
        self._accumulated_yaw = 0.0
        self._stop_ticks_remaining = 0
        return record.public()

    def stop(self, now: float, reason: str = "收到停止请求") -> dict[str, Any]:
        """抢占当前动作并安排多次零速度发布。"""

        if self._active is not None:
            self._finish(MotionStatus.CANCELLED, now, "CANCELLED", reason)
        else:
            # 即使当前空闲也重复发零速度，使停车请求具备明确的 ROS 输出。
            self._stop_ticks_remaining = max(
                self._stop_ticks_remaining, self.config.stop_publish_count
            )
        return self.status(now)

    def tick(self, now: float) -> VelocityCommand | None:
        """推进一次控制循环；调用者按固定频率发布返回速度。"""

        if self._active is None:
            # 动作结束后连续若干个周期返回零速度，降低单条消息丢失的风险。
            if self._stop_ticks_remaining > 0:
                self._stop_ticks_remaining -= 1
                return VelocityCommand(0.0, 0.0)
            return None

        record = self._active
        assert record.started_at is not None
        # 总动作超时优先于正常完成判断，确保控制不会无限运行。
        if now - record.started_at >= self.config.action_timeout:
            self._finish(
                MotionStatus.TIMED_OUT,
                now,
                "ACTION_TIMEOUT",
                "动作执行超过 60 秒",
            )
            return VelocityCommand(0.0, 0.0)
        if self._odom is None or now - self._odom.received_at > self.config.odom_timeout:
            # 运动过程中失去反馈时立即结束动作，并在本周期返回零速度。
            self._finish(
                MotionStatus.ODOM_TIMEOUT,
                now,
                "ODOM_TIMEOUT",
                "/odom 超过 1 秒未更新",
            )
            return VelocityCommand(0.0, 0.0)

        motion_type = MotionType(record.type)
        mode = MotionMode(record.mode)
        if mode is MotionMode.TIME:
            # 时间模式是开环终止条件，但仍要求 /odom 持续在线作为安全心跳。
            elapsed = now - record.started_at
            record.progress = min(elapsed, record.value)
            if elapsed >= record.value:
                self._finish(MotionStatus.SUCCEEDED, now)
                return VelocityCommand(0.0, 0.0)
            return self._velocity(motion_type, 1.0)

        if mode is MotionMode.DISTANCE:
            assert self._start_odom is not None
            # 前进取正方向，后退取负方向，使两者的 progress 都以正数增长。
            direction = 1.0 if motion_type is MotionType.FORWARD else -1.0
            dx = self._odom.x - self._start_odom.x
            dy = self._odom.y - self._start_odom.y
            # 将世界坐标位移投影到动作开始时的车头方向，而不是使用直线距离。
            projection = dx * math.cos(self._start_odom.yaw) + dy * math.sin(
                self._start_odom.yaw
            )
            record.progress = direction * projection
            error = record.value - record.progress
            if abs(error) <= self.config.distance_tolerance:
                self._finish(MotionStatus.SUCCEEDED, now)
                return VelocityCommand(0.0, 0.0)
            # 进入目标附近的减速区后逐渐降速；若越过目标，误差符号会使车辆反向修正。
            scale = self._speed_scale(
                abs(error),
                max(self.config.distance_tolerance * 4.0, record.value * 0.2),
            )
            return self._velocity(motion_type, math.copysign(scale, error))

        # 角度模式使用累计 yaw 而不是首尾 yaw 差，因此可正确处理跨越 ±π。
        direction = 1.0 if motion_type is MotionType.TURN_LEFT else -1.0
        progress_degrees = math.degrees(direction * self._accumulated_yaw)
        record.progress = progress_degrees
        error_degrees = record.value - progress_degrees
        if abs(error_degrees) <= self.config.angle_tolerance_degrees:
            self._finish(MotionStatus.SUCCEEDED, now)
            return VelocityCommand(0.0, 0.0)
        # 与距离模式相同，接近目标角度时减速，过冲后允许反向修正。
        scale = self._speed_scale(
            abs(error_degrees),
            max(self.config.angle_tolerance_degrees * 4.0, record.value * 0.2),
        )
        return self._velocity(motion_type, math.copysign(scale, error_degrees))

    def status(self, now: float) -> dict[str, Any]:
        """返回最小机器人与 Gateway 状态。"""

        # online 代表里程计是否新鲜，不代表 HTTP Server 或底盘所有组件均健康。
        odom_age = None if self._odom is None else max(0.0, now - self._odom.received_at)
        online = odom_age is not None and odom_age <= self.config.odom_timeout
        latest = next(reversed(self._records.values()), None) if self._records else None
        return {
            "online": online,
            "odom_age_seconds": odom_age,
            "pose": None
            if self._odom is None
            else {"x": self._odom.x, "y": self._odom.y, "yaw": self._odom.yaw},
            "velocity": None
            if self._odom is None
            else {
                "linear_x": self._odom.linear_x,
                "angular_z": self._odom.angular_z,
            },
            "gateway_status": "RUNNING" if self._active is not None else "IDLE",
            "operation_id": None
            if self._active is None
            else self._active.operation_id,
            "current_action_index": None,
            "last_operation": None if latest is None else latest.public(),
        }

    def operation(self, operation_id: str) -> dict[str, Any] | None:
        """查询一个已知动作。"""

        record = self._records.get(operation_id)
        return None if record is None else record.public()

    def _validate(self, command: MotionCommand) -> None:
        # 直线动作不能用角度终止，转向动作不能用距离终止。
        is_linear = command.type in {MotionType.FORWARD, MotionType.BACKWARD}
        if is_linear and command.mode not in {MotionMode.DISTANCE, MotionMode.TIME}:
            raise MotionRejected("INVALID_MODE", "直线动作只支持 distance 或 time")
        if not is_linear and command.mode not in {MotionMode.ANGLE, MotionMode.TIME}:
            raise MotionRejected("INVALID_MODE", "转向动作只支持 angle 或 time")
        if command.mode is MotionMode.DISTANCE:
            minimum, maximum, label = (
                self.config.min_distance,
                self.config.max_distance,
                "距离",
            )
        elif command.mode is MotionMode.ANGLE:
            minimum, maximum, label = (
                self.config.min_angle_degrees,
                self.config.max_angle_degrees,
                "角度",
            )
        else:
            minimum, maximum, label = (
                self.config.min_duration,
                self.config.max_duration,
                "时间",
            )
        # 所有目标量必须为正数且处于配置允许的安全范围。
        if not minimum <= command.value <= maximum:
            raise MotionRejected(
                "OUT_OF_RANGE", f"{label}必须在 {minimum:g} 到 {maximum:g} 之间"
            )

    def _velocity(self, motion_type: MotionType, scale: float) -> VelocityCommand:
        # 只允许线速度 X 和角速度 Z，其他 Twist 分量始终为零。
        if motion_type is MotionType.FORWARD:
            return VelocityCommand(self.config.linear_speed * scale, 0.0)
        if motion_type is MotionType.BACKWARD:
            return VelocityCommand(-self.config.linear_speed * scale, 0.0)
        if motion_type is MotionType.TURN_LEFT:
            return VelocityCommand(0.0, self.config.angular_speed * scale)
        return VelocityCommand(0.0, -self.config.angular_speed * scale)

    def _speed_scale(self, remaining: float, slow_zone: float) -> float:
        if remaining >= slow_zone:
            return 1.0
        ratio = remaining / slow_zone
        # 保留最低速度，避免速度过小导致底盘克服不了静摩擦。
        return max(self.config.slow_down_ratio, min(1.0, ratio))

    def _finish(
        self,
        status: MotionStatus,
        now: float,
        error_code: str | None = None,
        error: str | None = None,
    ) -> None:
        assert self._active is not None
        # 先固化终态供 Agent 查询，再清空本次动作使用的临时控制状态。
        self._active.status = status
        self._active.finished_at = now
        self._active.error_code = error_code
        self._active.error = error
        self._active = None
        self._start_odom = None
        self._last_yaw = None
        self._accumulated_yaw = 0.0
        # 后续 tick 将多次产生零速度命令，确保底盘收到停车指令。
        self._stop_ticks_remaining = self.config.stop_publish_count

    def _remember(self, record: MotionRecord) -> None:
        self._records[record.operation_id] = record
        self._records.move_to_end(record.operation_id)
        while len(self._records) > self.config.history_size:
            self._records.popitem(last=False)


def normalize_angle(angle: float) -> float:
    """把角度归一化到 [-π, π)。"""

    # 加 π 后取模再减 π，可将任意弧度值映射到统一区间。
    return (angle + math.pi) % (2.0 * math.pi) - math.pi

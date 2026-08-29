# xuegecar_sensor_fusion

ROS 2 Jazzy 上位机传感器融合功能包。它保留 MCU 原始 `/odom` 和 `/imu`，先执行宽松的物理硬门控，再由 `robot_localization` EKF 融合轮式前进速度、轮式角速度与 IMU Z 轴角速度。

EKF 的内部运动模型执行预测；轮式 `/odom` 提供 `twist.linear.x` 和
`twist.angular.z`，IMU 提供 `angular_velocity.z`。两路角速度共同更新 EKF
中的同一个 `vyaw` 状态，轮式角速度的默认权重约为 IMU 的 4 倍。MCU 已积分的
`x/y/yaw` 不进入 EKF，融合节点从自身的零原点推算一套独立位姿。

门控节点用四态运动状态机（静止/直行/原地转弯/弧线，见
`config/sensor_gate.yaml`）动态调整轮式角速度的置信度：直行时轮式
stddev=0.04（轮式:陀螺 ≈ 4:1）；原地转弯时放大到 0.16（反转为 1:4，
打滑时轮式角速度系统性偏大，改信陀螺）；弧线时取 0.08（1:1）。当前相位
随 `/fusion/motion_phase` 话题按里程计频率发布，并在 `/diagnostics` 中
以 `motion_phase` 字段上报。

当轮式 `|vx| <= 0.005 m/s` 且 `|angular.z| <= 0.01 rad/s` 连续保持
`0.2 s`，状态机进入静止态，将送入 EKF 的两路角速度都置零，抑制静止时 IMU
零偏造成的 yaw 漂移；检测到轮速超过阈值后立即恢复正常融合。因此，小车被
抬起但轮子不转时，外部旋转不会计入融合里程计。

## 数据流

```text
/odom -> sensor_gate_node -> /fusion/odom_valid --+
                                                   +-> ekf_node -> /odometry/filtered
/imu  -> sensor_gate_node -> /fusion/imu_valid  --+
```

`/odometry/filtered` 与原 `/odom` 一样使用 `nav_msgs/msg/Odometry`。第一版是旁路验证模式，不发布 TF，也不修改现有网关话题。

## 启动

```bash
cd /home/lk/car/software/leap_ros_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 launch xuegecar_sensor_fusion fusion.launch.py
```

检查输出：

```bash
ros2 topic hz /odometry/filtered
ros2 topic echo /diagnostics
```

## 参数来源

默认参数来自 `/home/lk/car/data/fusion_calibration_02`：

- 静止 IMU `gyro_z` 零偏约 `-0.00942 rad/s`；
- `/odom` 与 `/imu` 典型频率约 `50 Hz`；
- MCU 使用采样时的单调时钟，并在 micro-ROS 会话同步后通过固定偏移映射到
  ROS epoch；`/odom` 通过 best-effort 传输，避免可靠流积压旧速度；
- 主机保留 MCU 源时间，拒绝超过允许偏差、非单调或乱序的消息。断流超过
  `source_stamp_reset_after_gap` 后允许新会话安全重建时间基准；
- 马氏拒绝门限按 `fusion_calibration_02` 实测转弯幅度标定：odom `6σ`（覆盖 0.12 m/s）、IMU `10σ`（覆盖 0.8 rad/s）。

所有硬阈值、零偏和标准差都位于 `config/sensor_gate.yaml`，无需修改代码即可重新标定。

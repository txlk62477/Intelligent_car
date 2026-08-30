# xuegecar_sensor_fusion

ROS 2 Jazzy 上位机传感器融合功能包。它保留 MCU 原始 `/odom` 和 `/imu`，先执行宽松的物理硬门控，再由 `robot_localization` EKF 融合轮式前进速度、轮式角速度与 IMU Z 轴角速度。

## 重要：整个融合链必须统一使用仿真时间

WSL 的 `CLOCK_REALTIME` 已实测会在运行中突然回退约 `2.24 s`。因此本包默认
启动 `monotonic_clock_node`，以“启动时 epoch + `CLOCK_MONOTONIC_RAW` 已运行时间”按
`100 Hz` 发布严格递增的 `/clock`，并为门控和 EKF 设置
`use_sim_time: true`。这里的“仿真时间”不是仿真传感器数据，而是用 ROS 2
标准 `/clock` 机制给实车提供不会回退的逻辑时间轴。

以下规则是融合链的时间契约：

- 所有读取传感器时间戳、TF 或 `/odometry/filtered` 的节点必须使用
  `use_sim_time: true`；未来加入的 RViz、`robot_state_publisher`、SLAM、Nav2
  和定位节点也必须接入同一个 `/clock`。
- `monotonic_clock_node` 自身不能启用仿真时间；它使用不受频率校正影响的
  `CLOCK_MONOTONIC_RAW` 推进逻辑时间。
- 禁止在同一融合链中混用系统时间与 `/clock`，否则会出现 future、too old、
  TF extrapolation 或 sensor timeout。
- `/clock` 发布器停止后，所有使用仿真时间的定时器和超时判断都会暂停。
- MCU 时间只提供真实采样顺序和相邻 `dt`；主机不再把 MCU epoch 与可能回退的
  WSL 系统时间直接比较。
- 断流超过 `1 s` 后的第一条消息会被视为新会话基线；由于绝对 epoch 不可信，
  这一条无法再做“绝对过期”判断，后续消息则必须严格递增。

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
/clock <- monotonic_clock_node (CLOCK_MONOTONIC_RAW, 100 Hz)

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

`use_sim_time` 默认是 `true`。只有在确认操作系统时间稳定的原生 Linux 环境中，
才可显式关闭：

```bash
ros2 launch xuegecar_sensor_fusion fusion.launch.py use_sim_time:=false
```

检查输出：

```bash
ros2 topic hz /odometry/filtered
ros2 topic hz /clock
ros2 topic echo /diagnostics
```

## 录包与回放

录制时必须添加 `--use-sim-time`，并录下 `/clock`。在收到第一条 `/clock`
之前 rosbag 不会写入消息：

```bash
ros2 bag record --use-sim-time \
  -o /home/lk/car/data/distance_test_03 \
  /clock \
  /odom \
  /imu \
  /fusion/odom_valid \
  /fusion/imu_valid \
  /odometry/filtered \
  /fusion/motion_phase \
  /diagnostics \
  /tf \
  /tf_static
```

回放时先启动融合节点但关闭实车时钟发布器，再让 rosbag 从录制时间生成
`/clock`：

```bash
# 终端 A
ros2 launch xuegecar_sensor_fusion fusion.launch.py start_clock:=false

# 终端 B
ros2 bag play /home/lk/car/data/distance_test_03 --clock 100
```

回放前不要同时运行实车的 `monotonic_clock_node`，ROS 图中只能有一个
`/clock` 发布源。

## 参数来源

默认参数来自 `/home/lk/car/data/fusion_calibration_02`：

- 静止 IMU `gyro_z` 零偏约 `-0.00942 rad/s`；
- `/odom` 与 `/imu` 典型频率约 `50 Hz`；
- MCU 使用采样时的单调时钟；`/odom` 通过 best-effort 传输，避免可靠流积压
  旧速度；
- 主机 `TimeMapper` 保留 MCU 相邻采样 `dt`，映射到单调 `/clock`。它拒绝重复、
  回退和乱序时间；只有断流超过 `source_stamp_reset_after_gap` 时才重建基线。
  连续流的映射误差超过 `max_time_mapping_error` 时只上报
  `source_clock_drift_samples`，不修改 MCU 时间轴；
- 马氏拒绝门限按 `fusion_calibration_02` 实测转弯幅度标定：odom `6σ`（覆盖 0.12 m/s）、IMU `10σ`（覆盖 0.8 rad/s）。

所有硬阈值、零偏和标准差都位于 `config/sensor_gate.yaml`，无需修改代码即可重新标定。

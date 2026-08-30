# ROS 2 单调时间与仿真时间

## 为什么实车也使用仿真时间

这里的“仿真时间”只表示使用 ROS 2 标准 `/clock` 作为统一时间源，不表示
传感器或车辆是仿真的。小车仍然使用真实 MCU、编码器和 IMU。

WSL 的 `CLOCK_REALTIME` 已实测在运行中突然回退 `2.244632 s`。普通
`CLOCK_MONOTONIC` 虽然不回退，但仍会被 Hyper-V/内核做频率调整：实测它比
Windows 主机和 `CLOCK_MONOTONIC_RAW` 快约 `8%～10%`。micro-ROS 只能在
会话建立时对齐一次 epoch；
对齐后如果 WSL 墙上时间回退，MCU 消息就会被主机误判为“来自未来”。测试包
`distance_test_02` 中，移动阶段的 MCU 时间比主机接收时间超前约
`0.96～1.28 s`，导致全部非零速度被旧门控拒绝，EKF 没有累计移动距离。

因此融合系统使用：

```text
启动时的 system epoch + CLOCK_MONOTONIC_RAW 已运行时间 -> /clock
```

这个逻辑 ROS 时间保留接近真实日期的起点，但启动后只由
`CLOCK_MONOTONIC_RAW` 推进，不跟随 WSL 墙上时间的回退或频率校正。

## 时间架构

```text
monotonic_clock_node
  system epoch（只读取一次）
  + CLOCK_MONOTONIC_RAW elapsed
  -> /clock，100 Hz，严格递增

MCU /odom、/imu
  esp_timer 单调采样时间
  -> sensor_gate_node / TimeMapper
       保留 MCU 相邻采样 dt
       映射到 /clock
       拒绝重复、回退、乱序
  -> /fusion/odom_valid、/fusion/imu_valid
  -> robot_localization EKF
  -> /odometry/filtered
```

ROS 2 已提供 `use_sim_time`、`RCL_ROS_TIME` 和 `/clock` 订阅机制；本项目实现的
部分是实车 `monotonic_clock_node` 和 MCU→ROS 时间映射 `TimeMapper`。

## distance_test_03 回归结果

`distance_test_03` 的实测距离为 `1.0100 m`。旧实现使用受频率调整的
`RCL_STEADY_TIME` 推进 `/clock`，并在连续流中把 TimeMapper 强制跳到
`ros_now`，运动期间两次跳时合计约 `0.53 s`，使 EKF 多积分约 `0.14 m`。

使用 `CLOCK_MONOTONIC_RAW` 并禁止连续流跳时后，对同一份原始 `/odom`、`/imu`
重新融合的结果为：

| 数据 | 距离 | 相对实测误差 |
|---|---:|---:|
| MCU `/odom` | `1.0037 m` | `-0.63 cm` |
| 旧 `/odometry/filtered` | `1.1478 m` | `+13.78 cm` |
| 修复后 `/odometry/filtered` | `0.9990 m` | `-1.10 cm` |

修复后回放中 `source_stamp_resets=0`。这说明距离大误差来自时间轴跳变，不是
编码器比例或 EKF 速度观测配置。

`TimeMapper` 不再执行下面这种绝对 epoch 判断：

```text
abs(MCU stamp - WSL system now) <= 250 ms
```

它使用以下规则：

- 同一路传感器的 MCU 时间必须严格递增；重复、回退和乱序直接拒绝。
- 映射后的相邻时间差来自 MCU 采样差值，不来自 Wi-Fi 到达间隔。
- 主机使用 `CLOCK_MONOTONIC_RAW` 判断接收间隔，不受 WSL 校时和频率调整影响。
- 断流超过 `source_stamp_reset_after_gap`（默认 `1 s`）时重建基线。
- 连续数据中，映射与 `/clock` 偏差超过 `max_time_mapping_error`（默认 `0.25 s`）
  时只增加诊断字段 `source_clock_drift_samples`，绝不跳到 `ros_now`；EKF 的
  相邻测量 `dt` 始终来自 MCU `esp_timer`。
- 断流超过 1 秒后的第一条消息只能作为新会话基线；绝对 epoch 已不可信，
  因而无法对这一条做“绝对过期”判断，后续消息仍必须严格递增。

## 实车启动

```bash
cd /home/lk/car/software/leap_ros_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash

ros2 launch xuegecar_sensor_fusion fusion.launch.py
```

默认行为等价于：

```text
use_sim_time:=true
start_clock:=true
```

它会启动唯一的实车 `/clock` 发布器，并让 `sensor_gate_node` 与
`ekf_filter_node` 使用该时间。

检查：

```bash
ros2 topic info /clock --verbose
ros2 param get /sensor_gate_node use_sim_time
ros2 param get /ekf_filter_node use_sim_time
ros2 topic hz /odometry/filtered
ros2 topic echo /diagnostics
```

要求：

- `/clock` 的 `Publisher count` 必须是 `1`。
- 两个节点的 `use_sim_time` 必须是 `True`。
- `/diagnostics` 中 `rejected_timestamp` 不应在正常运行时持续增长。
- `/diagnostics` 中 `source_stamp_resets` 在连续运行时应保持为 `0`；
  `source_clock_drift_samples` 正常也应保持为 `0`。

注意：`ros2 topic delay /odom` 使用运行该命令的系统时间比较 MCU 原始 epoch，
在 WSL 中仍可能显示负延迟或秒级跳变。启用 TimeMapper 后，它不再是融合链的
有效健康指标；应检查 `/clock`、门控诊断和 `/odometry/filtered`。

## 录制 rosbag

录制节点也必须使用仿真时间，并录下 `/clock`。收到第一条 `/clock` 前，
rosbag 不会开始写消息。

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

## 回放 rosbag

回放时 rosbag 是唯一 `/clock` 发布器，所以必须关闭实车时钟节点：

```bash
# 终端 A：节点使用仿真时间，但不启动实车 /clock
ros2 launch xuegecar_sensor_fusion fusion.launch.py start_clock:=false

# 终端 B：由 rosbag 按 100 Hz 发布 /clock
ros2 bag play /home/lk/car/data/distance_test_03 --clock 100
```

不要同时运行 `monotonic_clock_node` 和 `ros2 bag play --clock`。

## 其他 ROS 2 节点如何接入

凡是读取传感器时间戳、TF、`/odometry/filtered` 或使用 ROS 时间做超时判断的
节点，都必须接入同一个 `/clock`：

- `robot_state_publisher`
- RViz
- SLAM、AMCL、Nav2 和定位节点
- TF 查询与发布节点
- 后续基于融合里程计做时间同步的节点

节点由 launch 启动时，传入：

```python
parameters=[..., {"use_sim_time": True}]
```

或者让上层 launch 提供统一参数：

```bash
ros2 launch <package> <file>.launch.py use_sim_time:=true
```

只发布无时间戳 `/cmd_vel`、不读取 TF 且用独立 monotonic clock 做安全超时的
简单控制输入节点可以不使用 `/clock`。但一个节点只要进入定位、导航或 TF
时间链，就不能混用系统时间。

## 后果与限制

- `/clock` 停止后，所有使用仿真时间的 ROS 定时器和超时判断都会暂停。
- 同一 ROS 图只能有一个 `/clock` 发布源。
- 逻辑时间启动后可能与 Windows 当前时间存在固定偏差，但距离、角度和 EKF
  的 `dt` 不受影响。
- 录包必须添加 `--use-sim-time`，回放必须明确发布 `/clock`。
- 在确认系统时间稳定的原生 Linux 环境，可关闭该方案：

  ```bash
  ros2 launch xuegecar_sensor_fusion fusion.launch.py \
    use_sim_time:=false start_clock:=false
  ```

- 不建议仅把旧的 ±250 ms 门限扩大到数秒；这样无法可靠区分旧包，并且下一次
  WSL 时间跳变仍可能越过新门限。

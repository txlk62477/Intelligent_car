# Web 上位机测试指南

当前先测试 Web，不需要启动 Agent Gateway、Nav2 或 Qt GUI。推荐使用第 3 节的实车链路；只看界面用第 2 节，无雷达且驱动轮架空时用第 4 节。

运动链路：

```text
Web → /cmd_vel_teleop → twist_mux → /cmd_vel_selected
    → collision_monitor → /cmd_vel → micro-ROS → MCU → 电机
```

Motion Controller 的 10 Hz 急停锁心跳和 0.5 秒失联锁止已删除。正常 Web 遥控不再需要它持续发送解锁心跳；现有 Web launch 仍包含控制核心中的 Motion Controller，用于显式急停/解锁服务。

链路末端的碰撞监控来自 `xuegecar_bringup` 包的 `collision_monitor`（不再是 `nav2_collision_monitor` 的原版可执行文件）。行为是：车身外扩 2 cm 的虚拟实体作为硬停车区，提前 1.0 s 按碰撞时间限速，停稳后若障碍只浅压在车头或车尾、另一侧干净，则允许最大 `0.03 m/s` 的直线退避。完整规则、参数和调参方法见 `docs/启动小车.md` 的“碰撞监控与 2 cm 虚拟实体”一节。改动该节点代码或参数后必须重新构建 `xuegecar_bringup` 并重启控制栈，方法见第 1 节。

## 1. 环境与构建

在工作区终端执行：

```bash
cd /home/lk/car/software/leap_ros_ws
source /opt/ros/jazzy/setup.bash
```

如果尚未创建包内 Python 环境：

```bash
python3 -m venv --system-site-packages src/xuegecar_web_gui/.venv
src/xuegecar_web_gui/.venv/bin/pip install fastapi uvicorn websockets pillow
```

当前安装的 `web_gui_node` 使用 `/usr/bin/python3`。仅激活 venv 不会改变这个入口的解释器，因此在启动 Web 的终端显式提供包内依赖路径：

```bash
export PYTHONPATH="/home/lk/car/software/leap_ros_ws/src/xuegecar_web_gui/.venv/lib/python3.12/site-packages:${PYTHONPATH:-}"
python3 -c 'import rclpy, fastapi, uvicorn, websockets; print("Web 依赖正常")'
colcon build --symlink-install --packages-select \
  xuegecar_bringup xuegecar_motion_controller xuegecar_web_gui
source install/setup.bash
```

上述构建适用于已经构建过的当前工作区。新工作区首次使用需先执行完整 `colcon build --symlink-install`，安装接口、描述、摄像头、传感器融合等依赖包。

每个新 ROS 终端都需执行：

```bash
cd /home/lk/car/software/leap_ros_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash
```

启动 Web 的终端还需设置上面的 `PYTHONPATH`。更新心跳代码后，应停止旧控制栈并重新启动，运行中的节点不会自动应用修改。

## 2. 只测试页面，不连接底盘

```bash
cd /home/lk/car/software/leap_ros_ws/src/xuegecar_web_gui
.venv/bin/python test/preview_server.py --host 0.0.0.0 --port 8080
```

电脑访问 `http://127.0.0.1:8080`，手机访问 `http://<主机局域网IP>:8080`。该服务使用真实前端和模拟状态、模拟画面，按钮不会控制车辆。可检查横竖屏布局、方向键、速度滑条和急停样式。

## 3. 只启动 Web 的实车测试（推荐）

首次架起驱动轮，先将线速度滑条调到约 `0.05 m/s`，角速度调到约 `0.2 rad/s`。确认停车操作后再落地测试。

### 终端 A：底盘通信

底盘 micro-ROS Agent 必须使用以下完整命令启动，只启动一个实例，不省略用户、IPC、共享内存或日志参数：

```bash
sudo docker run --rm -it \
  --name micro-ros-agent \
  --user "$(id -u):$(id -g)" \
  --network host \
  --ipc host \
  -e MICROROS_DISABLE_SHM=0 \
  -e ROS_LOG_DIR=/tmp/ros-log \
  microros/micro-ros-agent:${ROS_DISTRO:-jazzy} \
  udp4 --port 8888 -v6
```

确认 MCU 上电、联网，Agent 地址和端口与固件配置一致。检查底盘话题：

```bash
ros2 topic list | rg '^/(odom|imu|scan)$'
```

如果当前使用 MAVLink 串口通信，沿用 MAVLink 桥接方式，不再同时启动第二条底盘下发链路。以下检查以 micro-ROS 为例。

### 终端 B：传感器融合

```bash
ros2 launch xuegecar_sensor_fusion fusion.launch.py
```

它提供 `/clock`、`/scan_ts` 和 `/odometry/filtered`。传感器 TF 由随后启动的控制核心统一发布。

### 终端 C：Web 与控制核心

完成第 1 节环境设置后执行：

```bash
ros2 launch xuegecar_web_gui xuegecar_web_gui.launch.py \
  use_collision_monitor:=true \
  use_sim_time:=true \
  include_camera:=false \
  port:=8000
```

这个入口启动 Web、一个 `twist_mux`、碰撞监控、Motion Controller 和机器人状态发布节点，不启动 Gateway 或 Nav2。不要同时启动 `full_control.launch.py` 或另一套默认控制入口。

若已有控制核心，只启动 Web：

```bash
ros2 launch xuegecar_web_gui xuegecar_web_gui.launch.py \
  launch_control_core:=false \
  include_camera:=false \
  port:=8000
```

### 终端 D：摄像头（可选）

```bash
ros2 launch xuegecar_camera http_video_publisher.launch.py
```

当前配置地址为 `http://10.32.89.61:81/`，见 `src/xuegecar_camera/config/http_video_publisher.yaml`。摄像头不在线时，页面运动控制仍可单独测试。

### 浏览器访问

本机打开 `http://127.0.0.1:8000`，手机打开 `http://<主机局域网IP>:8000`。

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
```

预期返回 `200`。手机与主机需在同一可互通局域网。如果使用 WSL，手机访问 Windows WLAN 地址，需要将 8000/TCP 转发到当前 WSL IP 并放行防火墙。

## 4. 无雷达台架测试

仅在驱动轮架空、测试速度指令下发时使用：

```bash
ros2 launch xuegecar_web_gui xuegecar_web_gui.launch.py \
  use_collision_monitor:=false \
  use_sim_time:=false \
  include_camera:=false \
  port:=8000
```

仍需启动底盘通信。此模式不需要雷达、融合时钟或碰撞监控，链路缩短为：

```text
Web → /cmd_vel_teleop → twist_mux → /cmd_vel → MCU
```

此时没有 `/cmd_vel_selected` 输出，也没有碰撞保护。不要与第 3 节的 Web/control_core 同时启动。页面里程计在未启动融合时可能不显示，不影响台架速度下发测试。

## 5. 启动后检查

实车碰撞监控模式执行：

```bash
ros2 node list | rg 'twist_mux|collision_monitor|xuegecar_motion_controller|xuegecar_web_gui'
ros2 lifecycle get /collision_monitor
ros2 topic hz /clock
ros2 topic hz /scan_ts
ros2 topic hz /odometry/filtered
ros2 run tf2_ros tf2_echo odom base_link
ros2 run tf2_ros tf2_echo base_link laser_frame
```

`collision_monitor` 应为 `active`，扫描、时钟和 TF 应持续更新。`hz` 和 `tf2_echo` 会持续运行，用 Ctrl+C 结束后执行下一项。

核对下发端点：

```bash
ros2 topic info /cmd_vel_teleop -v
ros2 topic info /cmd_vel_selected -v
ros2 topic info /cmd_vel -v
ros2 param get /twist_mux locks.emergency_stop.timeout
```

预期 Web 发布 `/cmd_vel_teleop`，mux 发布 `/cmd_vel_selected`，碰撞监控发布 `/cmd_vel`，MCU 订阅最终速度。急停锁超时应为 `0.0`。台架模式跳过 `/cmd_vel_selected` 和碰撞监控检查，mux 直接发布 `/cmd_vel`。

核对碰撞监控的生效参数：

```bash
ros2 param get /collision_monitor FootprintApproach.time_before_collision
ros2 param get /collision_monitor VirtualStop.min_points
ros2 param get /collision_monitor hard_stop_padding
ros2 param get /collision_monitor escape_max_speed
ros2 param get /collision_monitor odom_topic
```

预期依次为 `1.0`、`3`、`0.02`、`0.03`、`/odometry/filtered`。任一数值不符说明
还在运行旧版本或加载了旧参数文件，应停止控制栈、重新构建 `xuegecar_bringup` 后
再启动。RViz 中添加 `/virtual_stop_polygon` 和 `/footprint_approach_polygon` 可看到
2 cm 硬保护区与提前减速区的实际形状。

**不再用 `ros2 topic hz /cmd_vel_emergency_lock` 检查启动是否正常。** 急停锁仅在服务调用时发布；正常启动后没有消息是预期行为。即使话题存在，`echo --once` 也可能一直等待下一次急停/解锁操作。

## 6. 运动与停车验收

1. 架起驱动轮，低速点击前进、后退、左转、右转，核对方向。前进 `linear.x > 0`，后退 `< 0`，左转 `angular.z > 0`，右转 `< 0`。
2. **方向键为锁存操作：点击后持续运动，松手不会停车。** 点击中间“停止”停车；桌面也可按空格或 K。W/A/S/D 和方向键同样会锁存方向。
3. 点击“停止”后，检查最终速度出现零值且轮子停止；零速度短时连发后 Web 进入静默，释放手动仲裁权。空闲时速度话题没有持续消息是正常的。
4. 在另一个终端先执行下面的急停锁订阅，再点击页面“急停锁”。应看到 `true`；点击“解锁”应看到 `false`。锁住后再尝试点击方向键，确认轮子保持停止。
5. 落地低速接近固定障碍物，核对 `/scan_ts` 持续更新和碰撞监控的三层行为：
   - 在 2 cm 保护区之外约“速度 × 1 s”的距离上（0.15 m/s 约 15 cm，0.3 m/s 约 30 cm）应先看到速度被压低（`/cmd_vel` 小于遥控命令），而不是直接停；
   - 进入车身外扩 2 cm 的硬保护区后 `/cmd_vel` 变为零且 `/collision_monitor_state` 显示停车；
   - 车头被挡住时点“后退”应能以约 `0.03 m/s` 直线退出保护区；旋转、边退边转、前后同时有障碍都应保持零速。
6. 正常关闭页面或让浏览器停止发送运动命令，检查 Web 后端停车与最终零速度。再恢复页面连接，确认可以继续控制。
7. 用第二台设备打开页面，确认单客户端控制权限制符合预期。

观察命令：

```bash
ros2 topic echo /cmd_vel_teleop
ros2 topic echo /cmd_vel
ros2 topic echo /cmd_vel_emergency_lock
```

以上分别在独立终端运行。急停锁话题没有周期消息，仅操作时出现消息。

“按停止”“正常关闭浏览器”和“强制杀死 Web 进程”是不同测试。进程被强制杀死时后端无法保证发送停车命令；当前已删除心跳失联锁止，MCU 指令超时停车尚未确认，不能将断进程等同于自动停车。需要验证时只在驱动轮架空条件下进行，并记录结果。

## 7. 运动中失灵时，按链路定位

复现前分别开终端观察 `/cmd_vel_teleop`、`/cmd_vel_selected`、`/cmd_vel`，记录失灵时刻和页面状态。台架模式跳过中间话题。

| 失灵时的现象 | 下一步检查 |
|---|---|
| 页面本机都打不开 | `ss -ltn`、本机 curl、Web 终端异常日志 |
| 本机打开，手机打不开 | 局域网、WSL 端口转发和防火墙 |
| 已选方向，但 `/cmd_vel_teleop` 无非零消息 | 页面连接/控制权、滑条是否为零、WebSocket 和 Web 日志 |
| 有手动非零速度，但 mux 无输出 | mux 是否唯一、显式急停锁、是否还有其他控制入口 |
| 有 `/cmd_vel_selected`，最终速度为零或无消息 | 碰撞监控生命周期、`/scan_ts`、TF 和传感器超时 |
| 一靠近障碍就停，没有先减速 | 是否运行旧版本节点、`FootprintApproach.time_before_collision` 是否为 `1.0` |
| 障碍在车头，后退键也无效 | 是否已停稳（`/odometry/filtered`）、是否在旋转、两侧是否都有障碍、障碍点是否已深入车身 5 mm 以上 |
| 一直在原地不动也不退出 | 只有前后同时有障碍或重叠过深才会拒绝退避，此时需人工移开；确认 `/collision_monitor_state` 与 RViz 保护区显示 |
| `/cmd_vel` 有非零消息，电机不动 | MCU 订阅端点、Agent 会话/串口、底盘供电和 MCU 日志 |
| 摄像头卡住且控制也失灵 | 同时记录摄像头和 Web 日志，确认手动速度是否也停止发布 |

解除已经设置的急停锁可点击页面“解锁”，或执行：

```bash
ros2 service call /motion/set_emergency_lock std_srvs/srv/SetBool '{data: false}'
```

查看碰撞监控状态：

```bash
ros2 lifecycle get /collision_monitor
ros2 topic echo /collision_monitor_state
ros2 topic hz /scan_ts
ros2 topic echo /virtual_stop_polygon
```

查看摄像头：

```bash
ros2 topic hz /camera/image_raw/compressed
```

仅凭“有时失灵”不能确认根因。心跳删除已完成，但仍需用上述话题记录验证失灵出现在哪一段。

## 8. 本次验收记录

- [ ] 页面可访问，单客户端控制权正常。
- [ ] 只有一个 mux，没有重复启动控制核心。
- [ ] 方向键点击后持续运动，停止键可停车。
- [ ] 急停发布 `true`，解锁发布 `false`，锁止期间车辆不动。
- [ ] mux 急停锁 timeout 为 `0.0`，没有周期心跳。
- [ ] 实车模式碰撞监控为 active，扫描和 TF 持续更新。
- [ ] 碰撞监控参数为 `time_before_collision=1.0`、`min_points=3`、`hard_stop_padding=0.02`、`escape_max_speed=0.03`。
- [ ] 接近障碍先限速，进入 2 cm 保护区后停车。
- [ ] 车头被挡时后退键可低速退出，旋转和前后夹住时保持停车。
- [ ] 正常关闭浏览器后实际停车。
- [ ] 记录失灵时三段速度话题及 Web/底盘日志。

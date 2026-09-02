# Web 上位机测试指南：模式 C（实车完整安全链）

本文只描述实车完整安全链测试。系统应由一个统一入口拥有
`robot_state_publisher`、`joint_state_publisher`、`twist_mux`、
`collision_monitor` 和 Motion Controller，避免重复启动控制节点。

## 1. 安全要求

- 首次测试架起驱动轮，或选择宽阔、无人的区域。
- 准备物理急停和断开电机电源的方法。
- 低速开始，默认最大线速度为 `0.3 m/s`。
- 确认急停按钮可触及后再让车辆落地。
- 不要同时单独启动 Gateway、Navigation2、Web GUI 的默认控制入口；完整组合只启动
  `full_control.launch.py`。

## 2. 构建与环境

```bash
cd /home/lk/car/software/leap_ros_ws
source /opt/ros/jazzy/setup.bash
source src/xuegecar_web_gui/.venv/bin/activate
colcon build --symlink-install
source install/setup.bash
```

如果尚未创建 Web GUI 虚拟环境：

```bash
cd /home/lk/car/software/leap_ros_ws/src/xuegecar_web_gui
python3 -m venv --system-site-packages .venv
.venv/bin/pip install fastapi uvicorn
```

手机和主机连接同一局域网。Web 服务监听 `0.0.0.0:8000`，手机访问：

```text
http://<主机局域网IP>:8000
```

本机先确认服务：

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
```

若 ROS 运行在 WSL，手机访问应使用 Windows 主机的 WLAN 地址，并确保 Windows 防火墙和端口转发已放行 `8000/TCP`，不能使用 WSL 的 `127.0.0.1`。

## 3. 启动顺序

### 3.1 启动 micro-ROS Agent

按照项目现有底盘启动方式启动 Agent，并确认只运行一个实例。典型 UDP 启动命令：

```bash
docker run --rm -it --net=host \
  --name micro-ros-agent \
  microros/micro-ros-agent:${ROS_DISTRO:-jazzy} \
  udp4 --port 8888
```

确认底盘话题已经出现：

```bash
ros2 topic list | grep -E '^/(odom|imu|scan)$'
```

### 3.2 启动传感器融合

```bash
ros2 launch xuegecar_sensor_fusion fusion.launch.py
```

融合节点提供单调 `/clock`、传感器门控、时间戳扫描和 `/odometry/filtered`。传感器 TF 由完整控制栈中的 `robot_state_publisher` 统一发布，不要再单独运行旧的静态 TF 发布命令。

检查：

```bash
ros2 topic hz /clock
ros2 topic hz /odometry/filtered
ros2 topic hz /scan_ts
ros2 run tf2_ros tf2_echo odom base_link
ros2 run tf2_ros tf2_echo base_link laser_frame
```

### 3.3 启动摄像头（需要画面时）

当前摄像头地址是 `10.32.89.61:81`：

```bash
ros2 launch xuegecar_camera http_video_publisher.launch.py
ros2 topic hz /camera/image_raw/compressed
```

配置文件为 `src/xuegecar_camera/config/http_video_publisher.yaml`。摄像头无信号时先用手机或电脑浏览器确认 `http://10.32.89.61:81/` 可访问；摄像头故障不应绕过 Collision Monitor 继续高速行驶。

### 3.4 启动完整控制栈和 Web

```bash
ros2 launch xuegecar_bringup full_control.launch.py \
  use_collision_monitor:=true \
  use_sim_time:=true \
  rviz:=false
```

该入口默认启动 Gateway、Navigation2、Web GUI，并且只启动一个控制核心。若只想测试 Web 与完整安全链，可关闭 Gateway 和 Navigation2：

```bash
ros2 launch xuegecar_bringup full_control.launch.py \
  launch_gateway:=false \
  launch_navigation:=false \
  launch_web_gui:=true \
  use_collision_monitor:=true \
  rviz:=false
```

浏览器打开：

```text
http://<主机局域网IP>:8000
```

## 4. 启动后检查

```bash
ros2 node list | grep -E '/twist_mux|/collision_monitor|/xuegecar_motion_controller'
ros2 lifecycle get /collision_monitor
ros2 topic info /cmd_vel_teleop -v
ros2 topic info /cmd_vel_selected -v
ros2 topic info /cmd_vel -v
ros2 topic hz /cmd_vel_emergency_lock
```

预期：

- `/twist_mux` 只有一个实例。
- `collision_monitor` 为 `active`。
- Web 发布 `/cmd_vel_teleop`。
- `twist_mux` 发布 `/cmd_vel_selected`。
- `collision_monitor` 发布最终 `/cmd_vel`。
- Motion Controller 是 `/cmd_vel_emergency_lock` 的唯一发布者。
- `/cmd_vel_emergency_lock` 正常解锁时为 `data: false`，急停锁定时为 `true`。

## 5. 实车验收步骤

1. 架起驱动轮，Web 滑条保持低速。
2. 短按前、后、左、右，确认方向和速度符号正确。
3. 松开摇杆，确认车辆停止，且手动命令在超时后释放仲裁权。
4. 按“急停锁”，确认 `/cmd_vel` 被锁止；按“解锁”后再继续。
5. 落地后以极低速直行，确认雷达能持续发布 `/scan_ts`。
6. 缓慢接近障碍物，确认 Collision Monitor 在预计 `0.3 s` 内碰撞时限制或停止速度。
7. 关闭浏览器或终止 Web 节点，确认 Web 看门狗使车辆停止。

不要用人员身体作为障碍物，也不要用高速验证碰撞保护。

## 6. 故障排查

### 浏览器打不开

```bash
ss -ltn | grep ':8000'
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
```

确认手机和主机在同一局域网；手机访问 Windows 主机 WLAN IP。若使用 WSL，检查 Windows `portproxy` 是否将 `0.0.0.0:8000` 转发到当前 WSL IP，并检查防火墙入站规则。

### 页面连接但车辆不动

```bash
ros2 topic hz /cmd_vel_teleop
ros2 topic hz /cmd_vel_selected
ros2 topic hz /cmd_vel
ros2 topic echo /cmd_vel_emergency_lock --once
```

- 急停心跳为 `true`：先解锁。
- 有 `/cmd_vel_teleop`、没有 `/cmd_vel_selected`：检查 `twist_mux` 和控制权占用。
- 有 `/cmd_vel_selected`、没有 `/cmd_vel`：检查 `collision_monitor` 是否 `active`、`/scan_ts` 和 TF 是否持续更新。
- `/scan_ts` 或 TF 不稳定时，不要关闭 Collision Monitor 强行驾驶。

### 摄像头没有画面

```bash
ros2 topic hz /camera/image_raw/compressed
```

确认 `http_video_publisher.yaml` 中为 `http://10.32.89.61:81/`，并从主机或手机先验证该地址可访问。

## 7. 最终验收清单

- [ ] 手机可以访问 Web 页面。
- [ ] 控制权只允许一台设备占用。
- [ ] 前后左右方向和速度符号正确。
- [ ] 松手后零速停车。
- [ ] 系统中只有一个 `twist_mux`。
- [ ] `collision_monitor` 为 `active`。
- [ ] Motion Controller 是急停心跳唯一发布者。
- [ ] 急停锁可以屏蔽所有速度源。
- [ ] `/scan_ts`、`/odometry/filtered` 和 TF 持续更新。
- [ ] 关闭 Web 后车辆停止。

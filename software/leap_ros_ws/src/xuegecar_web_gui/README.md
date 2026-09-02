# xuegecar_web_gui

XuegeCar **Web 上位机**（ROS2 节点 + 浏览器页面）。手机连同一局域网，浏览器输入 `http://<主机IP>:8000` 即可遥控小车。

第一阶段功能：摇杆/按键遥控、速度调节、实时摄像头画面、状态显示（速度/电池/摄像头）。

## 与 Qt 上位机（xuegecar_qt_gui）的关系

本包是 Qt 上位机的 Web 版。核心差异：

| | xuegecar_qt_gui | xuegecar_web_gui |
|---|---|---|
| 界面 | 桌面 Qt 窗口 | 手机/桌面浏览器 |
| 控制输入 | 键盘 W/S/A/D + 屏幕按钮 | 虚拟摇杆 + 上下左右按键（标签页切换） |
| 速度调节 | 线速度/角速度两个 SpinBox | 两个滑条（默认值与范围一致） |
| 摄像头 | QImage 解码显示 | 后端 MJPEG 流，页面 `<img>` 显示 |
| 速度发布 | 直接发 `/cmd_vel` | 发 `/cmd_vel_teleop`，走 twist_mux 速度仲裁 |

## 架构

```
手机浏览器 ──HTTP/WS──> FastAPI(uvicorn) ──> rclpy 节点（同进程）
                          │                     │
                     /stream: MJPEG      订阅 /camera/image_raw/compressed
                     /ws: 遥控命令       订阅 /odometry/filtered、电池话题
                                         发布 Twist -> /cmd_vel_teleop
                                         急停通过 Motion Controller 服务统一处理
```

### 速度仲裁链（沿用现有体系）

```
/cmd_vel_teleop (manual, 优先级 200, 本包发布)
/cmd_vel_agent   (agent,  优先级 150, motion_controller 发布)
/cmd_vel_nav     (nav,    优先级 100, Nav2 发布)
        └── twist_mux ──> /cmd_vel_selected ──> collision_monitor ──> /cmd_vel ──> micro-ROS ──> ESP32 底盘
急停锁: /cmd_vel_emergency_lock (优先级 255, 10Hz 心跳看门狗，心跳停=mux 锁死)
```

### 三层安全

1. **后端看门狗**：超过 `command_timeout`(0.3s) 无新命令 -> 自动零速发布；
2. **twist_mux 输入超时**：`/cmd_vel_teleop` 0.5s 无消息 -> 手动源失效；
3. **心跳锁死**：control_core 中 Motion Controller 维护解锁心跳，进程死亡 -> 心跳停 -> twist_mux 自动锁死全部速度源。

页面按钮：

- **停止**：立即发零速；
- **急停锁**：调用 `/motion/emergency_stop` 服务锁 twist_mux（导航/Agent 全部停）；
- **解锁**：调用 `/motion/set_emergency_lock`(false) 解除。

### 单客户端策略

同一时间只允许**一台设备**控制：第二个连接会被拒绝，页面显示"控制权被占用 + 当前控制者 IP"，待其断开（或会话超时 10s）后自动恢复。

## 构建（包内 venv）

```bash
cd /home/lk/car/software/leap_ros_ws/src/xuegecar_web_gui
python3 -m venv --system-site-packages .venv
.venv/bin/pip install fastapi uvicorn

cd /home/lk/car/software/leap_ros_ws
source /opt/ros/jazzy/setup.bash
source src/xuegecar_web_gui/.venv/bin/activate   # 使构建/运行都使用 venv Python
colcon build --packages-select xuegecar_web_gui --symlink-install
source install/setup.bash
```

> venv 使用 `--system-site-packages` 以便 import 系统 Python 里的 rclpy。

## 运行

```bash
# 独立模式（默认包含共享 control_core 和碰撞监控）
ros2 launch xuegecar_web_gui xuegecar_web_gui.launch.py

# 无雷达台架测试
ros2 launch xuegecar_web_gui xuegecar_web_gui.launch.py use_collision_monitor:=false

# 顺带拉起摄像头（IP 摄像头 MJPEG 地址）
ros2 launch xuegecar_web_gui xuegecar_web_gui.launch.py include_camera:=true camera_url:=http://<相机IP>/stream

# 已有 control_core 时不重复启动
ros2 launch xuegecar_web_gui xuegecar_web_gui.launch.py launch_twist_mux:=false
```

手机浏览器访问 `http://<主机IP>:8000`。

## 参数（config/xuegecar_web_gui.yaml）

| 参数 | 默认 | 说明 |
|---|---|---|
| `host` / `port` | `0.0.0.0` / `8000` | Web 服务监听地址与端口 |
| `camera_topic` | `/camera/image_raw/compressed` | 摄像头压缩图像话题 |
| `odom_topic` | `/odometry/filtered` | 里程计（实际速度显示） |
| `cmd_vel_topic` | `/cmd_vel_teleop` | 遥控 Twist 发布话题（twist_mux 手动输入） |
| `publish_rate_hz` | `10.0` | Twist 发布频率 |
| `command_timeout` | `0.3` | 后端看门狗超时（秒） |
| `session_timeout` | `10.0` | 控制会话空闲超时（秒） |
| `max_linear_cap` / `max_angular_cap` | `2.0` / `5.0` | 滑条硬上限 |
| `default_max_linear` / `default_max_angular` | `0.3` / `1.0` | 滑条默认值（与 Qt 包一致） |
| `estop_service` / `unlock_service` | `/motion/emergency_stop` / `/motion/set_emergency_lock` | 急停服务 |

## WebSocket 协议（/ws）

客户端 -> 服务端（JSON）：

| type | 字段 | 说明 |
|---|---|---|
| `cmd` | `linear`, `angular` | 速度命令（10Hz 持续发送，空闲发零） |
| `speed` | `max_linear`, `max_angular` | 滑条速度上限 |
| `stop` | - | 立即零速 |
| `estop` | - | 急停锁 |
| `unlock` | - | 解锁 |

服务端 -> 客户端：`welcome`(含 `token`，用于 `/stream?token=`)、`state`(状态快照 5Hz)、`busy`(被占用)、`ack`、`error`。

## 方向约定

摇杆上 = 前进；摇杆左 = 左转（`angular.z > 0`），与 `xuegecar_motion_controller` 注释约定一致。

# Intelligent Car

面向 LEAP ROS2 智能小车的上层软件二次开发项目。在保持现有硬件、单片机固件和 ESP32-S3 图传固件不变的前提下，重点开发建图、导航、视觉、控制、人机交互与智能 Agent 功能。

## 二次创作声明

本项目基于 [LEAP ROS2 开源机器人](https://github.com/czu963889306-dev/-ros2-) 和 [leap_ros_ws](https://github.com/czu963889306-dev/leap_ros_ws) 进行二次创作。原项目相关内容的著作权归原作者及贡献者所有；本项目的修改与扩展不代表原作者对本项目的认可或背书。

## 开发范围

本仓库只维护 ROS2 上层软件，主要包括：

- ROS2 节点、接口和启动流程
- 雷达接入、SLAM 建图与地图管理
- Nav2 自主导航与路径规划
- 摄像头数据接入和视觉功能
- 底盘通信与运动控制逻辑
- Qt 可视化控制界面
- URDF 机器人模型及相关参数配置
- 基于 LangGraph 的自然语言交互与任务编排

不在本仓库维护的内容包括硬件设计、单片机固件和 ESP32-S3 图传固件。

## ROS2 源码与来源

ROS2 源码已直接复制到本仓库中，作为普通项目文件维护；不保留上游仓库的 Git 历史，也不会自动同步上游更新。

| 模块 | 本地目录 | 原始仓库 | 引入版本 |
| --- | --- | --- | --- |
| ROS2 工作空间与功能包 | `software/leap_ros_ws/src` | [leap_ros_ws](https://github.com/czu963889306-dev/leap_ros_ws) | `c892276` |

LangGraph 项目使用官方 [Python 项目模板](https://github.com/langchain-ai/new-langgraph-project) 创建，并在 `agents/car_agent` 中独立构建和运行，不与 ROS2 工作空间混合。

## 底层固件参考

以下仓库仅作为通信协议、设备行为和问题排查的参考，本仓库不保存或维护其源码：

- 单片机驱动：[leap_ros_mcu_driver](https://github.com/czu963889306-dev/leap_ros_mcu_driver)
- ESP32-S3 WiFi 图传：[esp32_s3_wifi_camera](https://github.com/czu963889306-dev/esp32_s3_wifi_camera)

## ROS2 快速开始

```bash
git clone git@github.com:txlk62477/Intelligent_car.git
cd Intelligent_car
cd software/leap_ros_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

## LangGraph 智能 Agent 与 Robot Gateway

当前架构由 LangGraph 与三个 ROS2 节点组成，中间以本机 HTTP JSON 和 ROS2 Action 衔接：

- **LangGraph Agent Server**（`agents/car_agent`）：Supervisor 负责问答、状态、急停、短距离相对运动，用视觉大模型识别本地图片或相机当前帧，以及按需检测画面并委派视觉跟随子图（目标不存在时列出候选让用户选择）。
- **Robot Gateway**（`software/leap_ros_ws/src/xuegecar_agent_bridge`）：只负责 HTTP/ROS2 协议适配和状态汇总，通过 ROS2 Action 提交任务，不直接发布速度；缓存相机压缩帧与 YOLO 检测结果，可按需临时拉起 YOLO。
- **Motion Controller**（`software/leap_ros_ws/src/xuegecar_motion_controller`）：独占 `/cmd_vel`，执行相对位置控制和低速视觉跟随；同一时间只接受一个任务，急停除外。
- **Perception Manager**（`software/leap_ros_ws/src/xuegecar_perception`）：空闲时不加载 YOLO；视觉任务开始时启动 YOLO 子进程，任务结束、取消或超时后关闭进程并释放资源。

LangGraph 侧通过 `ROBOT_GATEWAY_URL`（默认 `http://127.0.0.1:8765`）调用 Gateway，不直接发布速度。视觉跟随根据目标框中心和相对初始面积控制方向与前后距离，默认运行 60 秒、最大 300 秒；当前不使用雷达避障。超过 3 米的长距离运动仍会被拒绝，后续交给 Nav2 Workflow。用户询问"当前画面/摄像头看到什么"且未给图片路径时，Agent 通过 Gateway 抓取相机最新帧（`/camera/image_raw/compressed`）保存到 `data/snapshots/` 再交给视觉模型识别；用户要求跟随某物体时，Agent 先读取当前 YOLO 检测列表（`/vision/detections`），目标不存在会列出候选物体让用户选择，存在则确认后开始跟随。

### 首次安装（Agent）

```bash
cd /home/lk/car/agents/car_agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env   # 在 .env 中填写 DeepSeek 密钥
```

### 启动

```bash
# 终端 1：Robot Gateway（需要小车或模拟器提供 /odom）
cd /home/lk/car/software/leap_ros_ws
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ros2 launch xuegecar_agent_bridge gateway.launch.py

# 终端 2：LangGraph Agent Server
cd /home/lk/car/agents/car_agent
source .venv/bin/activate
langgraph dev --no-browser
```

- Agent Server：`http://127.0.0.1:2024`，接口文档 `http://127.0.0.1:2024/docs`
- Robot Gateway：`http://127.0.0.1:8765`
  - `GET /v1/robot/status`
  - `POST /v1/motions`、`GET /v1/motions/{id}`
  - `POST /v1/follow-tasks`、`GET /v1/follow-tasks/{id}`
  - `POST /v1/follow-tasks/{id}/cancel`
  - `POST /v1/stop`
  - `GET /v1/camera/snapshot`（相机最新帧落盘到 `data/snapshots/`）
  - `GET /v1/perception/detections`（YOLO 检测列表；必要时临时拉起 YOLO）

实车运动需单独启动 Gateway 并确认周边安全；验证阶段不会主动让真实小车移动。

### 验证命令

```bash
# Agent：格式、类型、测试与本地加载
cd /home/lk/car/agents/car_agent
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
.venv/bin/mypy src
.venv/bin/python -m pytest tests/ -q          # 联网集成测试在未配置真实密钥时自动跳过

# ROS2：控制核心与 HTTP 接口测试 + 构建
cd /home/lk/car/software/leap_ros_ws
PYTHONPATH=src/xuegecar_motion_controller python3 -m pytest \
  src/xuegecar_motion_controller/test -q
PYTHONPATH=src/xuegecar_agent_bridge python3 -m pytest \
  src/xuegecar_agent_bridge/test -q
cd /home/lk/car/software/leap_ros_ws
source /opt/ros/jazzy/setup.bash
colcon build --packages-select leap_interfaces xuegecar_perception \
  xuegecar_motion_controller xuegecar_agent_bridge --symlink-install
```

`.env`、虚拟环境和 LangGraph 本地状态均不会提交到 Git。

## 目录结构

```text
.
├── agents/
│   └── car_agent/         # 独立 LangGraph 项目（Supervisor + 相对移动子图）
│       ├── src/agent/     # graph / supervisor / tools / workflows / common
│       ├── tests/         # 单元与集成测试
│       ├── langgraph.json # Agent Server 配置
│       └── pyproject.toml # Python 项目与依赖
├── software/
│   └── leap_ros_ws/       # ROS2 工作空间
│       ├── src/           # ROS2 功能包源码（含 xuegecar_agent_bridge）
│       ├── build/         # colcon 构建中间文件（不提交）
│       ├── install/       # 构建安装结果（不提交）
│       └── log/           # 构建日志（不提交）
├── docs/                  # 设计与规划文档
├── LICENSE                # GPL-2.0
└── THIRD_PARTY_NOTICES.md # 第三方版权与许可证声明
```

## 许可证

本仓库采用 [GPL-2.0](LICENSE) 许可证。引入的源码和模板仍归各自作者及贡献者所有，具体声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

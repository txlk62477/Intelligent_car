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

当前架构由两个独立进程组成，中间以本机 HTTP JSON 衔接：

- **LangGraph Agent Server**（`agents/car_agent`）：Supervisor 负责问答、状态查询、立即停车，以及把短距离相对运动（前进/后退/左转/右转，按距离/角度/时间）翻译成结构化动作列表，交给固定子图。子图先展示完整计划并等待人工确认，再逐条提交给 Robot Gateway，等待每个动作终态后执行下一个；任何失败都会停止剩余动作并回报失败步骤。
- **Robot Gateway**（`software/leap_ros_ws/src/xuegecar_agent_bridge`）：ROS2 Python 功能包，订阅 `/odom`、发布 `/cmd_vel`，在 ROS 主线程独占执行闭环控制（距离投影、跨 ±π 航向累计、接近目标降速、超时与断联急停），HTTP 线程只通过线程安全队列提交命令、读取状态快照。

LangGraph 侧通过 `ROBOT_GATEWAY_URL`（默认 `http://127.0.0.1:8765`）调用 Gateway，不直接发布速度。第一版不使用雷达、摄像头数据，不包含避障；超过 3 米的长距离运动会被拒绝，后续交给 Nav2 Workflow。

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
- Robot Gateway：`http://127.0.0.1:8765`（`GET /v1/robot/status`、`POST /v1/motions`、`GET /v1/motions/{id}`、`POST /v1/stop`）

实车运动需单独启动 Gateway 并确认周边安全；验证阶段不会主动让真实小车移动。

### 验证命令

```bash
# Agent：格式、类型、测试与本地加载
cd /home/lk/car/agents/car_agent
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
.venv/bin/mypy src
.venv/bin/python -m pytest tests/ -q          # 联网集成测试在未配置真实密钥时自动跳过

# Gateway：纯控制与 HTTP 接口测试 + 构建
cd /home/lk/car/software/leap_ros_ws/src/xuegecar_agent_bridge
python3 -m pytest test/ -q
cd /home/lk/car/software/leap_ros_ws
source /opt/ros/jazzy/setup.bash
colcon build --packages-select xuegecar_agent_bridge --symlink-install
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

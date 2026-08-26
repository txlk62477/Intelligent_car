# Intelligent Car

面向 LEAP ROS2 智能小车的 ROS2 上层软件二次开发项目。在保持现有硬件、单片机固件和 ESP32-S3 图传固件不变的前提下，重点开发建图、导航、视觉、控制与人机交互功能。

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

不在本仓库维护的内容包括硬件设计、单片机固件和 ESP32-S3 图传固件。

## ROS2 源码与来源

ROS2 源码已直接复制到本仓库中，作为普通项目文件维护；不保留上游仓库的 Git 历史，也不会自动同步上游更新。

| 模块 | 本地目录 | 原始仓库 | 引入版本 |
| --- | --- | --- | --- |
| ROS2 工作空间与功能包 | `software/leap_ros_ws/src` | [leap_ros_ws](https://github.com/czu963889306-dev/leap_ros_ws) | `c892276` |

## 底层固件参考

以下仓库仅作为通信协议、设备行为和问题排查的参考，本仓库不保存或维护其源码：

- 单片机驱动：[leap_ros_mcu_driver](https://github.com/czu963889306-dev/leap_ros_mcu_driver)
- ESP32-S3 WiFi 图传：[esp32_s3_wifi_camera](https://github.com/czu963889306-dev/esp32_s3_wifi_camera)

## 快速开始

```bash
git clone git@github.com:txlk62477/Intelligent_car.git
cd Intelligent_car
cd software/leap_ros_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

## 目录结构

```text
.
├── software/
│   └── leap_ros_ws/       # ROS2 工作空间
│       ├── src/           # ROS2 功能包源码
│       ├── build/         # colcon 构建中间文件（不提交）
│       ├── install/       # 构建安装结果（不提交）
│       └── log/           # 构建日志（不提交）
└── README.md
```

## 许可证

本仓库未另行指定统一许可证。引入的 `leap_ros_ws` 源码附带 GPL-2.0 许可证，使用和修改时请遵循其许可证及上游项目声明。

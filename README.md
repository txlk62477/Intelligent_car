# Intelligent Car

面向 LEAP ROS2 智能小车软件的学习与二次开发项目。目前主要关注软件功能，不包含原理图、BOM、PCB 和外壳等硬件资料。

## 二次创作声明

本项目基于 [LEAP ROS2 开源机器人](https://github.com/czu963889306-dev/-ros2-) 及其拆分出的软件源码进行二次创作。原项目相关内容的著作权归原作者及贡献者所有；本项目的修改与扩展不代表原作者对本项目的认可或背书。

## 软件源码与来源

源码已直接复制到本仓库中，作为普通项目文件维护；不保留上游仓库的 Git 历史，也不会自动同步上游更新。

| 模块 | 本地目录 | 原始仓库 | 引入版本 |
| --- | --- | --- | --- |
| ROS2 工作空间与功能包 | `software/leap_ros_ws` | [leap_ros_ws](https://github.com/czu963889306-dev/leap_ros_ws) | `c892276` |
| 单片机驱动 | `software/leap_ros_mcu_driver` | [leap_ros_mcu_driver](https://github.com/czu963889306-dev/leap_ros_mcu_driver) | `1c06c0da` |
| ESP32-S3 WiFi 摄像头图传 | `software/esp32_s3_wifi_camera` | [esp32_s3_wifi_camera](https://github.com/czu963889306-dev/esp32_s3_wifi_camera) | `551f39e` |

## 项目状态

三个上游软件模块已引入。后续开发将以 ROS2 功能、底层控制和 WiFi 图传的软件修改与扩展为主。

## 快速开始

```bash
git clone git@github.com:txlk62477/Intelligent_car.git
cd Intelligent_car
```

## 目录结构

```text
.
├── software/
│   ├── leap_ros_ws/             # ROS2 工作空间与功能包
│   ├── leap_ros_mcu_driver/     # 单片机底层控制代码
│   └── esp32_s3_wifi_camera/    # ESP32-S3 WiFi 图传代码
└── README.md
```

## 许可证

本仓库未另行指定统一许可证。引入的源码应遵循对应上游仓库附带的许可证及使用声明，其中 `leap_ros_ws` 附带 GPL-2.0 许可证。对于未附带独立许可证文件的模块，请勿自行推定额外授权范围。

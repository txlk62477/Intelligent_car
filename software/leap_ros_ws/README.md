# leap_ros_ws

ROS 2 workspace for the xuegecar stack.

## Workspace layout

ROS2 packages live under `src/`. Run builds from this workspace root so that
`build/`, `install/`, and `log/` remain separate from the source tree.

```bash
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

## Packages

- `src/camsense_lidar`: Camsense 串口雷达驱动，发布扫描数据。
- `src/leap_interfaces`: 给底层固件和上位机用的自定义服务接口。
- `src/openslam_gmapping`: 经典 GMapping SLAM 代码包。
- `src/slam_gmapping`: ROS 2 下的 GMapping 封装与启动配置。
- `src/xuegecar_bringup`: 整车启动包，统一加载 URDF、状态发布、速度仲裁和碰撞监控。
- `src/xuegecar_camera`: HTTP/MJPEG 摄像头流转 ROS 图像消息。
- `src/xuegecar_cartographer`: Cartographer 建图相关配置与启动文件。
- `src/xuegecar_description`: 车体模型、URDF 和描述资源。
- `src/xuegecar_mavlink_bridge`: MAVLink 到 ROS 2 的串口桥接。
- `src/xuegecar_navigation2`: Nav2 导航、地图和 RViz 配置。
- `src/xuegecar_qt_gui`: 主 Qt 操作台，集成导航、相机和雷达视图。
- `src/xuegeros_demo`: 总启动入口，串起底盘、micro-ROS 和外设流程。

## Notes

- `xuegecar_servo_qt_gui` has been removed from this workspace.
- `ydlidar_ros2_driver` is no longer referenced by `xuegeros_demo`.

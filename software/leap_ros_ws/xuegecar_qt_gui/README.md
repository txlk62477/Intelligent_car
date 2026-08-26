# xuegecar_qt_gui

ROS2 + C++ Qt5 上位机，用于显示地图、Nav2 costmap/path、雷达扫描点、机器人位置、摄像头图像、电池电压和电量，并发布速度控制、Nav2 目标点和初始位姿。

## 构建

```bash
cd /home/xuegeros/xuegeros_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select xuegecar_qt_gui --symlink-install
source install/setup.bash
```

## 启动

```bash
ros2 launch xuegecar_qt_gui xuegecar_qt_gui.launch.py
```

也可以直接运行：

```bash
ros2 run xuegecar_qt_gui xuegecar_qt_gui
```

## 操作

- `W/S`: 前进/后退。
- `A/D`: 左转/右转。
- `Space`: 急停。
- 可在右侧设置最大线速度和最大角速度。
- 地图左键拖拽发布 `/goal_pose` 导航目标，拖拽方向就是目标朝向。
- 点击“设置位姿”后，在地图左键拖拽发布 `/initialpose` 初始位姿。
- 地图右键或中键拖动平移，滚轮缩放，点击“地图复位”恢复自适应视图。
- “启动命令”面板可启动或停止 Micro-ROS、底层驱动、摄像头和 SLAM+Nav2。
- “ROS数据”面板可刷新所有话题，查看 `ros2 topic info -v`，并对任意话题执行一次 `ros2 topic echo --once`。
- 上位机启动的 launch 命令会放入独立 session；关闭窗口或点击停止时会清理整个进程组，避免 launch 子进程残留。

默认话题：

- 摄像头：`/camera/image_raw/compressed`
- 雷达：`/scan`
- 地图：`/map`
- 里程计：`/odom`
- 速度控制：`/cmd_vel`
- 导航目标：`/goal_pose`
- 初始位姿：`/initialpose`
- 电池：优先订阅 `/battery_state`，并兼容 `/voltage`、`/battery_voltage`、`/battery_percent`
- 路径：`/plan`、`/global_plan`、`/local_plan`、`/received_global_plan`、`/transformed_global_plan`
- Costmap：`/global_costmap/costmap`、`/global_costmap/costmap_raw`、`/local_costmap/costmap`、`/local_costmap/costmap_raw`

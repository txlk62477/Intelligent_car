# camsense_lidar

ROS2 driver for a Camsense serial lidar. The protocol parser was ported from the ESP32
driver that was placed in `xuegeros_ws/src/camsense_lidar.cpp`; ESP-IDF UART code was
replaced by Linux `termios`, and full rotations are published as `sensor_msgs/msg/LaserScan`.

## Build

```bash
cd ~/xuegeros_ws
colcon build --packages-select camsense_lidar
source install/setup.bash
```

## Run

```bash
ros2 launch camsense_lidar camsense_lidar.launch.py
```

The node publishes `/scan` with `frame_id=laser_frame`.

Important parameters are in `params/camsense_lidar.yaml`:

- `port`: serial device path.
- `baudrate`: serial baud rate, default `115200`.
- `center_base_angle`: lidar angle offset in degrees, default `198.5` for a 180-degree rotated installation.
- `clockwise`: mirrors the published scan direction, default `true` for this installation.

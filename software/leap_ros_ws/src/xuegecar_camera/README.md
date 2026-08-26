# xuegecar_camera

ROS2 node that reads an HTTP/MJPEG video stream and publishes
`sensor_msgs/msg/CompressedImage`. Raw `sensor_msgs/msg/Image` publishing can
be enabled when needed.

The stream parameters are configured in:

```bash
xuegecar_camera/config/http_video_publisher.yaml
```

## Build

```bash
cd ~/xuegeros_ws
colcon build --packages-select xuegecar_camera
source install/setup.bash
```

## Run

```bash
ros2 launch xuegecar_camera http_video_publisher.launch.py
```

The node publishes compressed images on `/camera/image_raw/compressed` with
`frame_id=camera_link`. By default it does not publish `/camera/image_raw`,
because raw RGB frames use much more ROS bandwidth and can make HTTP camera
streams stutter on small computers or Wi-Fi.

Use a different YAML file:

```bash
ros2 launch xuegecar_camera http_video_publisher.launch.py \
  params_file:=/path/to/http_video_publisher.yaml
```

Override parameters from launch if needed:

```bash
ros2 launch xuegecar_camera http_video_publisher.launch.py \
  url:=http://192.168.31.197:81/ \
  topic:=/camera/image_raw/compressed \
  raw_topic:=/camera/image_raw \
  frame_id:=camera_link \
  fps:=20.0 \
  jpeg_quality:=70
```

Enable raw image publishing only if a consumer requires `/camera/image_raw`:

```bash
ros2 launch xuegecar_camera http_video_publisher.launch.py publish_raw:=true
```

View the image stream:

```bash
ros2 run rqt_image_view rqt_image_view
```

For compressed transport, run:

```bash
ros2 run rqt_image_view rqt_image_view --ros-args -p image_transport:=compressed
```

Then select `/camera/image_raw`.

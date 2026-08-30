"""模拟相机发布器：把一张本地图片按固定帧率编码成 JPEG 发布到图像话题。

用于在真实相机不可用时测试 yolo_detect_node 的完整链路。
"""

from __future__ import annotations

import cv2
import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Header


def _video_qos() -> QoSProfile:
    """与真实相机发布者一致的 QoS。"""
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.BEST_EFFORT,
    )


class SimCameraPublisher(Node):
    def __init__(self) -> None:
        super().__init__("sim_camera_publisher")

        self.declare_parameter("topic", "/camera/image_raw/compressed")
        self.declare_parameter(
            "image_path", "/home/lk/car/test/yolo_detection/samples/bus.jpg"
        )
        self.declare_parameter("fps", 20.0)
        self.declare_parameter("jpeg_quality", 85)
        self.declare_parameter("frame_id", "camera_link")

        self.topic = self.get_parameter("topic").get_parameter_value().string_value
        image_path = self.get_parameter("image_path").get_parameter_value().string_value
        fps = self.get_parameter("fps").get_parameter_value().double_value
        self._jpeg_quality = max(
            1, min(100, self.get_parameter("jpeg_quality").get_parameter_value().integer_value)
        )
        self._frame_id = self.get_parameter("frame_id").get_parameter_value().string_value

        frame = cv2.imread(image_path)
        if frame is None:
            raise ValueError(f"无法读取测试图片: {image_path}")
        self._frame = frame

        self._publisher = self.create_publisher(
            CompressedImage, self.topic, _video_qos()
        )
        period = 1.0 / fps if fps > 0.0 else 0.0
        self._timer = self.create_timer(period, self._publish)
        self.get_logger().info(
            f"模拟相机: {image_path} ({self._frame.shape[1]}x{self._frame.shape[0]}) "
            f"→ {self.topic} @{fps:g}fps"
        )

    def _publish(self) -> None:
        ok, encoded = cv2.imencode(
            ".jpg", self._frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]
        )
        if not ok:
            return
        msg = CompressedImage()
        msg.header = Header()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self._frame_id
        msg.format = "jpeg"
        msg.data = encoded.tobytes()
        self._publisher.publish(msg)

    def destroy_node(self) -> None:
        super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = SimCameraPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

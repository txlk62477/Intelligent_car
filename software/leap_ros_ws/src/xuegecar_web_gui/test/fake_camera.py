"""测试用：向 /camera/image_raw/compressed 发布假 JPEG 帧（10Hz）。"""

import numpy as np
import cv2
import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage


class FakeCamera(Node):
    def __init__(self):
        super().__init__("fake_camera")
        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST, depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )
        self._pub = self.create_publisher(
            CompressedImage, "/camera/image_raw/compressed", qos
        )
        self._counter = 0
        self._timer = self.create_timer(0.1, self._tick)

    def _tick(self):
        # 渐变彩色帧 + 帧序号文字，便于肉眼确认流在更新。
        self._counter += 1
        h, w = 240, 320
        img = np.zeros((h, w, 3), dtype=np.uint8)
        hue = (self._counter * 7) % 180
        img[:] = (0, 0, 0)
        img[:, :] = cv2.cvtColor(
            np.full((h, w, 3), hue, dtype=np.uint8), cv2.COLOR_HSV2BGR
        )
        cv2.putText(
            img, f"frame {self._counter}", (10, 40),
            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2,
        )
        ok, encoded = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            return
        msg = CompressedImage()
        msg.format = "jpeg"
        msg.data = encoded.tobytes()
        msg.header.stamp = self.get_clock().now().to_msg()
        self._pub.publish(msg)


def main():
    rclpy.init()
    node = FakeCamera()
    rclpy.spin(node)


if __name__ == "__main__":
    main()

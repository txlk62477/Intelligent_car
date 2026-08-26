import time
import threading

import cv2
import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy
from rclpy.qos import QoSProfile
from rclpy.qos import ReliabilityPolicy
from sensor_msgs.msg import Image
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Header


class HttpVideoPublisher(Node):
    def __init__(self):
        super().__init__("http_video_publisher")

        self.declare_parameter("url", "")
        self.declare_parameter("topic", "/camera/image_raw/compressed")
        self.declare_parameter("raw_topic", "/camera/image_raw")
        self.declare_parameter("frame_id", "camera_link")
        self.declare_parameter("fps", 30.0)
        self.declare_parameter("reconnect_delay", 2.0)
        self.declare_parameter("use_ffmpeg", True)
        self.declare_parameter("jpeg_quality", 80)
        self.declare_parameter("publish_raw", False)

        self.url = self.get_parameter("url").get_parameter_value().string_value
        self.topic = self.get_parameter("topic").get_parameter_value().string_value
        self.raw_topic = self.get_parameter("raw_topic").get_parameter_value().string_value
        self.frame_id = self.get_parameter("frame_id").get_parameter_value().string_value
        self.fps = self.get_parameter("fps").get_parameter_value().double_value
        self.reconnect_delay = (
            self.get_parameter("reconnect_delay").get_parameter_value().double_value
        )
        self.use_ffmpeg = (
            self.get_parameter("use_ffmpeg").get_parameter_value().bool_value
        )
        self.jpeg_quality = (
            self.get_parameter("jpeg_quality").get_parameter_value().integer_value
        )
        self.publish_raw = (
            self.get_parameter("publish_raw").get_parameter_value().bool_value
        )

        if not self.url:
            raise ValueError("Parameter 'url' must be set in the camera YAML file.")
        self.jpeg_quality = max(1, min(100, self.jpeg_quality))

        self.capture = None
        self.stop_event = threading.Event()
        self.frame_lock = threading.Lock()
        self.latest_frame = None
        self.latest_frame_seq = 0
        self.published_frame_seq = 0
        self.last_frame_time = 0.0
        self.frame_period = 1.0 / self.fps if self.fps > 0.0 else 0.0
        video_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )
        self.publisher = self.create_publisher(CompressedImage, self.topic, video_qos)
        self.raw_publisher = (
            self.create_publisher(Image, self.raw_topic, video_qos)
            if self.publish_raw and self.raw_topic
            else None
        )
        timer_period = 0.005 if self.frame_period <= 0.0 else min(0.005, self.frame_period)
        self.timer = self.create_timer(timer_period, self.publish_next_frame)
        self.capture_thread = threading.Thread(target=self.capture_loop, daemon=True)
        self.capture_thread.start()

        self.get_logger().info(
            f"Publishing video stream {self.url} to {self.topic} as compressed jpeg "
            f"with frame_id={self.frame_id}; publish_raw={self.publish_raw}"
        )

    def open_capture(self):
        if self.capture is not None:
            self.capture.release()
            self.capture = None

        backend = cv2.CAP_FFMPEG if self.use_ffmpeg else cv2.CAP_ANY
        capture = cv2.VideoCapture(self.url, backend)
        if not capture.isOpened():
            self.get_logger().warn(
                f"Failed to open video stream: {self.url}; retrying in "
                f"{self.reconnect_delay:.1f}s"
            )
            time.sleep(max(0.1, self.reconnect_delay))
            return False

        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.capture = capture
        self.get_logger().info(f"Connected to video stream: {self.url}")
        return True

    def capture_loop(self):
        while rclpy.ok() and not self.stop_event.is_set():
            if self.capture is None and not self.open_capture():
                continue

            ok, frame = self.capture.read()
            if not ok or frame is None:
                self.get_logger().warn(
                    f"Lost video stream: {self.url}; retrying in "
                    f"{self.reconnect_delay:.1f}s"
                )
                self.capture.release()
                self.capture = None
                self.stop_event.wait(max(0.1, self.reconnect_delay))
                continue

            with self.frame_lock:
                self.latest_frame = frame
                self.latest_frame_seq += 1

    def publish_next_frame(self):
        now = time.monotonic()
        if self.frame_period > 0.0 and now - self.last_frame_time < self.frame_period:
            return

        with self.frame_lock:
            frame = self.latest_frame
            frame_seq = self.latest_frame_seq
        if frame is None or frame_seq == self.published_frame_seq:
            return

        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
        )
        if not ok:
            self.get_logger().warn("Failed to encode frame as jpeg.")
            return

        msg = CompressedImage()
        msg.header = Header()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.frame_id
        msg.format = "jpeg"
        msg.data = encoded.tobytes()
        self.publisher.publish(msg)

        if self.raw_publisher is not None:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            raw_msg = Image()
            raw_msg.header = msg.header
            raw_msg.height = rgb_frame.shape[0]
            raw_msg.width = rgb_frame.shape[1]
            raw_msg.encoding = "rgb8"
            raw_msg.is_bigendian = False
            raw_msg.step = raw_msg.width * 3
            raw_msg.data = rgb_frame.tobytes()
            self.raw_publisher.publish(raw_msg)

        self.published_frame_seq = frame_seq
        self.last_frame_time = now

    def destroy_node(self):
        self.stop_event.set()
        if hasattr(self, "capture_thread") and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = HttpVideoPublisher()
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

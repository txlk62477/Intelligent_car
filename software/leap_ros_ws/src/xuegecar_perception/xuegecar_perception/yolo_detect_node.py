"""YOLO 检测节点：订阅压缩图像话题，持续输出识别结果与标注视频流。

订阅 /camera/image_raw/compressed (sensor_msgs/CompressedImage, jpeg)，
解码后用 test/yolo_detection 的 YoloDetector 推理，然后发布：
  /vision/detections              leap_interfaces/Detections (类型化检测结果)
  /vision/annotated/compressed    sensor_msgs/CompressedImage (标注帧, jpeg 压缩)
  /vision/annotated               sensor_msgs/Image (标注帧, 裸 bgr8,
                                    rqt_image_view 默认 transport 直接可看)

环境说明：onnxruntime/cv2/numpy 来自 test/yolo_detection/.venv（由 launch 通过
PYTHONPATH 提供），rclpy 来自系统；本文件本身保持零额外依赖。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import rclpy
from leap_interfaces.msg import Detection, Detections
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import CompressedImage, Image
from std_msgs.msg import Header


def _video_qos() -> QoSProfile:
    """与相机发布者一致：BEST_EFFORT + KEEP_LAST(1)，自然丢弃旧帧。"""
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=1,
        reliability=ReliabilityPolicy.BEST_EFFORT,
    )


class YoloDetectNode(Node):
    def __init__(self) -> None:
        super().__init__("yolo_detect_node")

        self.declare_parameter("image_topic", "/camera/image_raw/compressed")
        self.declare_parameter("result_topic", "/vision/detections")
        self.declare_parameter("annotated_topic", "/vision/annotated/compressed")
        self.declare_parameter("annotated_raw_topic", "/vision/annotated")
        self.declare_parameter("publish_annotated_raw", False)
        self.declare_parameter(
            "model_path", "/home/lk/car/test/yolo_detection/models/yolov8n.onnx"
        )
        self.declare_parameter("yolo_module_dir", "/home/lk/car/test/yolo_detection")
        self.declare_parameter("imgsz", 640)
        self.declare_parameter("conf", 0.25)
        self.declare_parameter("iou", 0.45)
        self.declare_parameter("device", "auto")
        self.declare_parameter("log_period_s", 1.0)
        self.declare_parameter("jpeg_quality", 85)
        self.declare_parameter("warmup_timeout_s", 20.0)

        self.image_topic = self.get_parameter("image_topic").get_parameter_value().string_value
        self.result_topic = self.get_parameter("result_topic").get_parameter_value().string_value
        self.annotated_topic = (
            self.get_parameter("annotated_topic").get_parameter_value().string_value
        )
        self.annotated_raw_topic = (
            self.get_parameter("annotated_raw_topic").get_parameter_value().string_value
        )
        self._publish_annotated_raw = (
            self.get_parameter("publish_annotated_raw").get_parameter_value().bool_value
        )
        self._jpeg_quality = max(
            1, min(100, self.get_parameter("jpeg_quality").get_parameter_value().integer_value)
        )
        self._log_period = self.get_parameter("log_period_s").get_parameter_value().double_value
        self._warmup_timeout = (
            self.get_parameter("warmup_timeout_s").get_parameter_value().double_value
        )

        # 引入 YOLO 模块：单点依赖 test/yolo_detection，避免复制代码。
        module_dir = self.get_parameter("yolo_module_dir").get_parameter_value().string_value
        resolved = str(Path(module_dir).expanduser().resolve())
        if resolved not in sys.path:
            sys.path.insert(0, resolved)
        from yolo_detect.detector import YoloDetector, draw_detections

        model_path = self.get_parameter("model_path").get_parameter_value().string_value
        if not Path(model_path).is_file():
            raise FileNotFoundError(f"YOLO 模型不存在: {model_path}")
        self._draw_detections = draw_detections
        self._model_path = model_path
        self._imgsz = self.get_parameter("imgsz").get_parameter_value().integer_value
        self._conf = self.get_parameter("conf").get_parameter_value().double_value
        self._iou = self.get_parameter("iou").get_parameter_value().double_value
        self._requested_device = (
            self.get_parameter("device").get_parameter_value().string_value
        )
        self.detector = YoloDetector(
            model_path=model_path,
            imgsz=self._imgsz,
            conf_threshold=self._conf,
            iou_threshold=self._iou,
            device=self._requested_device,
        )
        backend = self.detector.engine.backend
        if self._requested_device != "cpu" and ("CUDA" in backend or "TensorRT" in backend):
            self._warmup_or_fallback()
        self.get_logger().info(
            f"[detector] backend={self.detector.engine.backend} "
            f"model={model_path} 推理输入={self.detector.imgsz}"
        )

        qos = _video_qos()
        self.subscription = self.create_subscription(
            CompressedImage, self.image_topic, self._on_image, qos
        )
        self.result_pub = self.create_publisher(Detections, self.result_topic, qos)
        self.annotated_pub = self.create_publisher(CompressedImage, self.annotated_topic, qos)
        self.annotated_raw_pub = (
            self.create_publisher(Image, self.annotated_raw_topic, qos)
            if self._publish_annotated_raw and self.annotated_raw_topic
            else None
        )

        # 统计窗口
        self._window_start = time.monotonic()
        self._window_frames = 0
        self._last_log = time.monotonic()

        extra = f", 标注裸流 {self.annotated_raw_topic}" if self.annotated_raw_pub else ""
        self.get_logger().info(
            f"订阅 {self.image_topic} → 结果 {self.result_topic}, "
            f"标注流 {self.annotated_topic}{extra}"
        )

    def _warmup_or_fallback(self) -> None:
        """GPU 预热并检测挂起；超时/失败则自动回退 CPU。

        WSL2 的 GPU 深度待机后，新进程第一次 CUDA 调用可能永久挂起
        （此前实测 cuInit=304 或首帧卡死）。预热带超时，避免节点启动后卡死。
        """
        done = threading.Event()
        outcome: dict = {}

        def _warmup() -> None:
            try:
                outcome["avg_ms"] = self.detector.engine.warmup(self._imgsz, times=1)
            except Exception as error:  # noqa: BLE001
                outcome["error"] = error
            finally:
                done.set()

        thread = threading.Thread(target=_warmup, daemon=True, name="yolo-gpu-warmup")
        thread.start()
        if not done.wait(timeout=self._warmup_timeout):
            self.get_logger().error(
                "GPU 预热超时（疑似 WSL 驱动深度待机未唤醒），自动回退 CPU 推理。"
            )
            self._fallback_to_cpu()
            return
        if "error" in outcome:
            self.get_logger().error(
                f"GPU 预热失败: {outcome['error']}，自动回退 CPU 推理。"
            )
            self._fallback_to_cpu()
            return
        self.get_logger().info(f"GPU 预热完成，平均 {outcome['avg_ms']:.1f}ms")

    def _fallback_to_cpu(self) -> None:
        from yolo_detect.detector import YoloDetector

        self.detector = YoloDetector(
            model_path=self._model_path,
            imgsz=self._imgsz,
            conf_threshold=self._conf,
            iou_threshold=self._iou,
            device="cpu",
        )
        self.get_logger().warn(f"已回退 CPU: backend={self.detector.engine.backend}")

    def _on_image(self, msg: CompressedImage) -> None:
        frame = cv2.imdecode(
            np.frombuffer(msg.data, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if frame is None:
            self.get_logger().warn("图像解码失败，跳过该帧。")
            return

        started = time.perf_counter()
        result = self.detector.detect(frame)
        total_ms = (time.perf_counter() - started) * 1000.0

        self._window_frames += 1

        # 1) 发布类型化检测结果
        detections_msg = Detections()
        detections_msg.header = Header()
        detections_msg.header.stamp = msg.header.stamp  # 保留源帧时间戳
        detections_msg.header.frame_id = msg.header.frame_id
        detections_msg.image_width = int(frame.shape[1])
        detections_msg.image_height = int(frame.shape[0])
        detections_msg.inference_ms = float(result.inference_ms)
        detections_msg.total_ms = float(total_ms)
        for det in result.detections:
            d = Detection()
            d.label = det.label
            d.score = float(det.score)
            d.x1, d.y1, d.x2, d.y2 = (float(v) for v in det.box)
            detections_msg.detections.append(d)
        self.result_pub.publish(detections_msg)

        # 2) 发布标注视频流
        annotated = self._draw_detections(frame, result.detections)
        # 2a) jpeg 压缩版（省带宽，需压缩插件查看）
        ok, encoded = cv2.imencode(
            ".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]
        )
        if ok:
            out = CompressedImage()
            out.header = Header()
            out.header.stamp = self.get_clock().now().to_msg()
            out.header.frame_id = msg.header.frame_id
            out.format = "jpeg"
            out.data = encoded.tobytes()
            self.annotated_pub.publish(out)
        # 2b) 裸像素版 bgr8（rqt 默认 transport 直接可看，无插件依赖）
        if self.annotated_raw_pub is not None:
            raw = Image()
            raw.header = Header()
            raw.header.stamp = self.get_clock().now().to_msg()
            raw.header.frame_id = msg.header.frame_id
            raw.height = annotated.shape[0]
            raw.width = annotated.shape[1]
            raw.encoding = "bgr8"
            raw.is_bigendian = False
            raw.step = annotated.shape[1] * 3
            raw.data = annotated.tobytes()
            self.annotated_raw_pub.publish(raw)

        # 3) 定时日志
        now = time.monotonic()
        if now - self._last_log >= self._log_period:
            window_fps = self._window_frames / max(1e-9, now - self._window_start)
            labels = [det.label for det in result.detections[:5]]
            self.get_logger().info(
                f"帧#{self._window_frames} 检测={len(result.detections)} "
                f"推理={result.inference_ms:.1f}ms 端到端={total_ms:.1f}ms "
                f"实时≈{window_fps:.1f}fps 目标={labels}"
            )
            self._last_log = now
            self._window_start = now
            self._window_frames = 0

    def destroy_node(self) -> None:
        super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = None
    try:
        node = YoloDetectNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            try:
                node.destroy_node()
            except KeyboardInterrupt:
                pass
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

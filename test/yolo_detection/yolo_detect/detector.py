"""YoloDetector 高层 API：图 -> 检测结果列表。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .engine import InferenceEngine
from .postprocess import Detection, decode_and_nms, to_detections
from .preprocess import letterbox, to_net_input


@dataclass
class FrameResult:
    detections: list[Detection] = field(default_factory=list)
    inference_ms: float = 0.0   # 纯推理耗时
    total_ms: float = 0.0       # 预处理+推理+后处理


class YoloDetector:
    def __init__(
        self,
        model_path: str,
        imgsz: int = 640,
        conf_threshold: float = 0.25,
        iou_threshold: float = 0.45,
        device: str = "auto",
    ):
        self.imgsz = imgsz
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        self.engine = InferenceEngine(model_path, device=device)
        print(f"[detector] backend: {self.engine.backend}")

    def detect(self, frame_bgr: np.ndarray) -> FrameResult:
        t0 = time.perf_counter()

        lb = letterbox(frame_bgr, self.imgsz)
        blob = to_net_input(lb.image, self.imgsz)

        t1 = time.perf_counter()
        raw = self.engine.run(blob)
        t2 = time.perf_counter()

        per_class_boxes, per_class_scores = decode_and_nms(
            raw, self.conf_threshold, self.iou_threshold
        )
        detections = to_detections(per_class_boxes, per_class_scores, lb)
        t3 = time.perf_counter()

        return FrameResult(
            detections=detections,
            inference_ms=(t2 - t1) * 1000.0,
            total_ms=(t3 - t0) * 1000.0,
        )

    def detect_bgr(self, frame_bgr: np.ndarray) -> FrameResult:
        return self.detect(frame_bgr)


def draw_detections(
    frame_bgr: np.ndarray,
    detections: list[Detection],
) -> np.ndarray:
    """在图上画检测框与标签，返回新图。"""
    out = frame_bgr.copy()
    for det in detections:
        x1, y1, x2, y2 = [int(v) for v in det.box]
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
        label = f"{det.label} {det.score:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(
            out, (x1, max(0, y1 - th - 6)), (x1 + tw + 4, y1), (0, 255, 0), -1
        )
        cv2.putText(
            out, label, (x1 + 2, max(0, y1 - 3)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
        )
    return out

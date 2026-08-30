"""YOLOv8 预处理：letterbox + 归一化 + NCHW 布局。

与 Ultralytics 导出的 ONNX（84x8400 输出）约定一致：
- 输入: NCHW float32, 值域 [0,1], 通道顺序 RGB
- 输出: [1, 84, 8400] 的转置由 postprocess 处理
"""

from __future__ import annotations

import cv2
import numpy as np


class LetterboxResult:
    """letterbox 后的图像及其逆变换信息。"""

    def __init__(
        self,
        image: np.ndarray,
        scale: float,
        pad_x: int,
        pad_y: int,
        original_shape: tuple[int, int],
    ):
        self.image = image            # 已 letterbox 的 BGR 图
        self.scale = scale            # 缩放比例
        self.pad_x = pad_x            # 左右填充像素
        self.pad_y = pad_y            # 上下填充像素
        self.original_shape = original_shape  # (h, w)

    def to_original(self, x1: float, y1: float, x2: float, y2: float) -> tuple[float, float, float, float]:
        """把 letterbox 坐标系下的框映射回原图坐标。"""
        x1 = (x1 - self.pad_x) / self.scale
        y1 = (y1 - self.pad_y) / self.scale
        x2 = (x2 - self.pad_x) / self.scale
        y2 = (y2 - self.pad_y) / self.scale
        h, w = self.original_shape
        x1 = max(0.0, min(x1, w - 1.0))
        y1 = max(0.0, min(y1, h - 1.0))
        x2 = max(0.0, min(x2, w - 1.0))
        y2 = max(0.0, min(y2, h - 1.0))
        return x1, y1, x2, y2


def letterbox(
    image_bgr: np.ndarray,
    size: int = 640,
    fill: int = 114,
) -> LetterboxResult:
    """等比缩放并填充到 size x size，保持纵横比（YOLO 标准做法）。"""
    h, w = image_bgr.shape[:2]
    scale = min(size / h, size / w)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(image_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    pad_x = (size - new_w) // 2
    pad_y = (size - new_h) // 2
    canvas = np.full((size, size, 3), fill, dtype=np.uint8)
    canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized
    return LetterboxResult(canvas, scale, pad_x, pad_y, (h, w))


def to_net_input(letterboxed_bgr: np.ndarray, size: int = 640) -> np.ndarray:
    """BGR uint8 -> NCHW float32 [1,3,size,size]，RGB 顺序，值域 [0,1]。"""
    rgb = cv2.cvtColor(letterboxed_bgr, cv2.COLOR_BGR2RGB)
    blob = rgb.astype(np.float32) / 255.0
    blob = np.transpose(blob, (2, 0, 1))  # CHW
    return blob[np.newaxis, ...]          # NCHW

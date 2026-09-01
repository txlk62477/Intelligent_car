"""YOLOv8 后处理：84x8400 输出解码 + 类别无关 NMS。

Ultralytics 导出的 ONNX 输出形状为 [1, 84, 8400]：
- 前 4 行: cx, cy, w, h（letterbox 坐标系，640 尺度）
- 后 80 行: COCO 80 类置信度
- 8400 = 3 个检测头 (80x80 + 40x40 + 20x20)
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

COCO_NAMES: tuple[str, ...] = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
)


@dataclass
class Detection:
    label: str
    score: float
    box: tuple[float, float, float, float]  # xyxy, 原图坐标


def decode_and_nms(
    output: np.ndarray,
    conf_threshold: float,
    iou_threshold: float,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """从 [1,84,8400] 输出解码，每候选取最高类别后统一 NMS。

    Returns:
        (per_class_boxes, per_class_scores): 每个元素为 array(N,4) xyxy 与 array(N,) 分数，
        框坐标仍在 letterbox(640) 坐标系，需经 to_original 映射回原图。
    """
    preds = output[0]  # [84, 8400]
    if preds.shape[0] != 84:
        raise ValueError(f"unexpected output shape: {output.shape}")

    boxes = preds[:4].T          # [8400, 4] cxcywh
    scores = preds[4:].T         # [8400, 80]
    best_classes = np.argmax(scores, axis=1)
    best_scores = scores[np.arange(scores.shape[0]), best_classes]

    cx, cy, w, h = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    xyxy = np.stack(
        [cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0], axis=1
    )

    valid = np.where(best_scores >= conf_threshold)[0]
    kept_boxes = np.empty((0, 4), dtype=np.float32)
    kept_scores = np.empty((0,), dtype=np.float32)
    kept_classes = np.empty((0,), dtype=np.int64)
    if valid.size:
        cand_boxes = xyxy[valid]
        cand_scores = best_scores[valid]
        cand_classes = best_classes[valid]
        cand_nms_boxes = np.column_stack(
            (cand_boxes[:, :2], cand_boxes[:, 2:] - cand_boxes[:, :2])
        )
        kept = cv2.dnn.NMSBoxes(
            cand_nms_boxes.tolist(),
            cand_scores.tolist(),
            conf_threshold,
            iou_threshold,
        )
        if kept is not None and len(kept):
            kept = np.asarray(kept).reshape(-1)
            kept_boxes = cand_boxes[kept]
            kept_scores = cand_scores[kept]
            kept_classes = cand_classes[kept]

    per_class_boxes: list[np.ndarray] = []
    per_class_scores: list[np.ndarray] = []
    for cls in range(scores.shape[1]):
        class_mask = kept_classes == cls
        per_class_boxes.append(kept_boxes[class_mask])
        per_class_scores.append(kept_scores[class_mask])
    return per_class_boxes, per_class_scores


def to_detections(
    per_class_boxes: list[np.ndarray],
    per_class_scores: list[np.ndarray],
    letterbox_result,
) -> list[Detection]:
    """映射回原图坐标，组装成 Detection 列表（按分数降序）。"""
    detections: list[Detection] = []
    for cls, boxes in enumerate(per_class_boxes):
        if boxes.shape[0] == 0:
            continue
        for box, score in zip(boxes, per_class_scores[cls]):
            x1, y1, x2, y2 = letterbox_result.to_original(*box)
            detections.append(
                Detection(COCO_NAMES[cls], float(score), (x1, y1, x2, y2))
            )
    detections.sort(key=lambda d: d.score, reverse=True)
    return detections

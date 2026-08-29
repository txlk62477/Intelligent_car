"""可切换 Provider 的图像识别模块。"""

from agent.vision.factory import build_vision_recognizer, get_vision_recognizer
from agent.vision.recognizer import (
    VisionRecognitionError,
    VisionRecognizer,
    VisionResult,
)

__all__ = [
    "VisionRecognitionError",
    "VisionRecognizer",
    "VisionResult",
    "build_vision_recognizer",
    "get_vision_recognizer",
]

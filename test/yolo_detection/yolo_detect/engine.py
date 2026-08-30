"""推理引擎：onnxruntime(CUDA/FP16) 优先，OpenCV DNN(CPU) 兜底。

根据实际机器配置选择：
- RTX 4050 + WSL2 CUDA 直通 -> onnxruntime-gpu, CUDAExecutionProvider + FP16
- 无 GPU/未装 onnxruntime -> OpenCV DNN CPU（零额外依赖）
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np


class InferenceEngine:
    """封装两种后端，提供统一 run() 接口。"""

    def __init__(self, model_path: str, device: str = "auto"):
        self.model_path = model_path
        self.device = device
        self.backend = "none"
        self.session = None
        self.net = None
        self.input_name = None
        self.input_shape = None
        self._load()

    def _load(self) -> None:
        try:
            self._load_onnxruntime()
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[engine] onnxruntime 不可用 ({exc})，回退 OpenCV DNN CPU")

        try:
            self._load_opencv()
            return
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"所有后端加载失败: {exc}") from exc

    # ---- onnxruntime ----

    def _load_onnxruntime(self) -> None:
        import onnxruntime as ort

        providers = []
        if self.device in ("auto", "gpu"):
            available = ort.get_available_providers()
            if "CUDAExecutionProvider" in available:
                providers = [
                    (
                        "CUDAExecutionProvider",
                        {"device_id": 0, "arena_extend_strategy": "kSameAsRequested"},
                    ),
                    "CPUExecutionProvider",
                ]
            elif "TensorrtExecutionProvider" in available:
                providers = ["TensorrtExecutionProvider", "CPUExecutionProvider"]
        if not providers:
            providers = ["CPUExecutionProvider"]

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.intra_op_num_threads = 0

        session = ort.InferenceSession(
            self.model_path, sess_options=so, providers=providers
        )
        self.input_name = session.get_inputs()[0].name
        self.input_shape = session.get_inputs()[0].shape
        self.session = session
        self.backend = "onnxruntime:" + session.get_providers()[0]

    def _ort_run(self, blob: np.ndarray) -> np.ndarray:
        assert self.session is not None
        return self.session.run(None, {self.input_name: blob})[0]

    # ---- OpenCV DNN ----

    def _load_opencv(self) -> None:
        import cv2

        net = cv2.dnn.readNetFromONNX(self.model_path)
        net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.net = net
        self.backend = "opencv_dnn:CPU"

    def _opencv_run(self, blob: np.ndarray) -> np.ndarray:
        assert self.net is not None
        self.net.setInput(blob)
        return self.net.forward()

    # ---- 统一接口 ----

    def run(self, blob: np.ndarray) -> np.ndarray:
        """输入 NCHW float32 blob，返回 [1,84,8400] 原始输出。"""
        if self.session is not None:
            return self._ort_run(blob)
        return self._opencv_run(blob)

    def warmup(self, imgsz: int = 640, times: int = 3) -> float:
        """预热并返回平均推理耗时(ms)。"""
        blob = np.zeros((1, 3, imgsz, imgsz), dtype=np.float32)
        for _ in range(times):
            self.run(blob)
        start = time.perf_counter()
        n = 10
        for _ in range(n):
            self.run(blob)
        return (time.perf_counter() - start) * 1000.0 / n

    def __repr__(self) -> str:
        return f"InferenceEngine(backend={self.backend}, model={self.model_path})"

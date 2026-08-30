"""延迟 + CPU/GPU 占用基准：GPU 与 CPU 两阶段，各跑 PHASE_SEC 秒。

输出：
- 分阶段延迟（预处理/推理/后处理）与端到端每帧延迟的 p50/p95
- 推理期间 GPU 利用率/显存/功耗（nvidia-smi 采样）
- 推理期间本进程 CPU 占用（/proc/<pid>/stat 采样，按核百分比）
"""

from __future__ import annotations

import os
import statistics
import subprocess
import threading
import time

import cv2
import numpy as np

from yolo_detect.detector import YoloDetector
from yolo_detect.preprocess import letterbox, to_net_input
from yolo_detect.postprocess import decode_and_nms, to_detections

import argparse

PHASE_SEC = 15
IMAGE = "samples/bus.jpg"
MODEL = "models/yolov8n.onnx"
IMG_W, IMG_H = 640, 480  # 模拟相机分辨率

# ---------- 资源采样 ----------
gpu_utils: list[float] = []
gpu_mems: list[float] = []
gpu_powers: list[float] = []
cpu_pcts: list[float] = []
_stop = threading.Event()


def _cpu_pct() -> float:
    """ps 的 %cpu（寿命均值，阶段足够长时收敛到稳态）。"""
    try:
        out = subprocess.run(
            ["ps", "-o", "%cpu=", "-p", str(os.getpid())],
            capture_output=True, text=True, timeout=3,
        )
        return float(out.stdout.strip())
    except Exception:
        return float("nan")


def sampler():
    while not _stop.is_set():
        time.sleep(0.5)
        cpu_pcts.append(_cpu_pct())
        try:
            out = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,power.draw",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True, text=True, timeout=3,
            )
            parts = out.stdout.strip().split(",")
            gpu_utils.append(float(parts[0]))
            gpu_mems.append(float(parts[1]))
            gpu_powers.append(float(parts[2]))
        except Exception:
            pass


def pct(xs, p):
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * p))]


def phase(device: str, frame: np.ndarray, model: str) -> None:
    det = YoloDetector(model, device=device)
    det.engine.warmup(640)
    print(f"\n=== 阶段: {device} ({det.engine.backend})  model={model}，持续 {PHASE_SEC}s ===")

    pre, infer, post, total = [], [], [], []
    t_end = time.perf_counter() + PHASE_SEC
    frames = 0
    while time.perf_counter() < t_end:
        t0 = time.perf_counter()
        lb = letterbox(frame, 640)
        blob = to_net_input(lb.image, 640)
        t1 = time.perf_counter()
        raw = det.engine.run(blob)
        t2 = time.perf_counter()
        pb, ps = decode_and_nms(raw, det.conf_threshold, det.iou_threshold)
        dets = to_detections(pb, ps, lb)
        t3 = time.perf_counter()
        pre.append((t1 - t0) * 1000)
        infer.append((t2 - t1) * 1000)
        post.append((t3 - t2) * 1000)
        total.append((t3 - t0) * 1000)
        frames += 1

    print(f"  处理帧数: {frames}  吞吐: {frames / PHASE_SEC:.1f} fps")
    for name, xs in (("预处理", pre), ("推理", infer), ("后处理", post), ("端到端", total)):
        print(f"  {name}: p50={pct(xs, 0.5):6.2f}ms  p95={pct(xs, 0.95):6.2f}ms  max={max(xs):6.2f}ms")
    if gpu_utils:
        print(
            f"  GPU: util avg={statistics.mean(gpu_utils):.0f}% max={max(gpu_utils):.0f}%  "
            f"mem avg={statistics.mean(gpu_mems):.0f}MiB  "
            f"power avg={statistics.mean(gpu_powers):.0f}W" if gpu_powers else ""
        )
    if cpu_pcts:
        vals = [c for c in cpu_pcts if c == c]  # 去 NaN
        vals = vals[len(vals) // 2 :]  # 丢弃前半段（寿命均值收敛期）
        print(
            f"  CPU(本进程, 全核合计): avg={statistics.mean(vals):.0f}%  "
            f"max={max(vals):.0f}%  (16 核全满 = 1600%)"
        )


def main() -> None:
    global PHASE_SEC
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=str, default=MODEL)
    ap.add_argument("--phase", type=int, default=PHASE_SEC)
    args = ap.parse_args()
    PHASE_SEC = args.phase

    frame = cv2.imread(IMAGE)
    if frame is None:
        raise SystemExit(f"无法读取 {IMAGE}")
    frame = cv2.resize(frame, (IMG_W, IMG_H))  # 模拟相机帧
    print(f"输入: {IMAGE} 模拟 {IMG_W}x{IMG_H}")

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    try:
        for device in ("gpu", "cpu"):
            phase(device, frame, args.model)
    finally:
        _stop.set()
        th.join(timeout=2)


if __name__ == "__main__":
    main()

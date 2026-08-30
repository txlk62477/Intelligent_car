"""YOLO 检测 CLI：单图 / 视频 / MJPEG 相机流。

用法:
    python3 run_detect.py --input samples/bus.jpg --save output/bus_out.jpg
    python3 run_detect.py --input http://192.168.31.188:81/ --camera
    python3 run_detect.py --input test.mp4 --save output/test_out.mp4

    --model 指定 models/ 下的 onnx（默认 yolov8n.onnx）
    --device auto|gpu|cpu   推理后端（auto: GPU 可用则 GPU）
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import cv2

from yolo_detect.detector import YoloDetector, draw_detections

ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = ROOT / "models" / "yolov8n.onnx"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="YOLOv8 常见物体检测（COCO 80 类）")
    p.add_argument("--input", type=str, default=None, help="图片/视频路径或 MJPEG URL")
    p.add_argument("--camera", action="store_true", help="输入为 MJPEG 相机流")
    p.add_argument("--model", type=str, default=str(DEFAULT_MODEL), help="ONNX 模型路径")
    p.add_argument("--imgsz", type=int, default=640, help="推理输入尺寸")
    p.add_argument("--conf", type=float, default=0.25, help="置信度阈值")
    p.add_argument("--iou", type=float, default=0.45, help="NMS IoU 阈值")
    p.add_argument("--device", type=str, default="auto", choices=["auto", "gpu", "cpu"])
    p.add_argument("--save", type=str, default=None, help="保存输出路径（图片/视频）")
    p.add_argument("--show", action="store_true", help="弹窗显示（需要 GUI 环境）")
    p.add_argument("--warmup", type=int, default=2, help="预热轮数")
    return p.parse_args()


def detect_image(detector: YoloDetector, path: str, args: argparse.Namespace) -> None:
    frame = cv2.imread(path)
    if frame is None:
        raise SystemExit(f"无法读取图片: {path}")
    result = detector.detect(frame)
    print(
        f"[image] {path}: {len(result.detections)} detections, "
        f"inference={result.inference_ms:.1f}ms total={result.total_ms:.1f}ms"
    )
    for d in result.detections[:10]:
        print(f"    {d.label:14s} {d.score:.3f}  box={tuple(round(v, 1) for v in d.box)}")
    if args.save:
        out = draw_detections(frame, result.detections)
        os.makedirs(os.path.dirname(os.path.abspath(args.save)) or ".", exist_ok=True)
        cv2.imwrite(args.save, out)
        print(f"[image] 标注图已保存: {args.save}")
    if args.show:
        cv2.imshow("yolo", draw_detections(frame, result.detections))
        cv2.waitKey(0)
        cv2.destroyAllWindows()


def detect_stream(detector: YoloDetector, url: str, args: argparse.Namespace) -> None:
    cap = cv2.VideoCapture(url)
    if not cap.isOpened():
        raise SystemExit(f"无法打开视频/流: {url}")
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    writer = None
    if args.save:
        fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
        writer = cv2.VideoWriter(
            args.save, cv2.VideoWriter_fourcc(*"mp4v"), fps, (640, 480)
        )

    print(f"[stream] {url}  按 Ctrl+C 退出")
    frame_count, infer_sum, total_sum = 0, 0.0, 0.0
    t_start = time.perf_counter()
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            result = detector.detect(frame)
            frame_count += 1
            infer_sum += result.inference_ms
            total_sum += result.total_ms
            if frame_count % 20 == 0:
                elapsed = time.perf_counter() - t_start
                print(
                    f"[stream] frames={frame_count} fps={frame_count / elapsed:.1f} "
                    f"avg_infer={infer_sum / frame_count:.1f}ms "
                    f"avg_total={total_sum / frame_count:.1f}ms "
                    f"detections={len(result.detections)}"
                )
            if args.save and writer is not None:
                writer.write(draw_detections(frame, result.detections))
            if args.show:
                cv2.imshow("yolo", draw_detections(frame, result.detections))
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()
    if frame_count:
        print(
            f"[stream] 结束: {frame_count} 帧, "
            f"avg_infer={infer_sum / frame_count:.1f}ms, "
            f"avg_total={total_sum / frame_count:.1f}ms"
        )


def main() -> None:
    args = parse_args()
    if not os.path.exists(args.model):
        raise SystemExit(
            f"模型不存在: {args.model}\n"
            f"请先运行: python3 scripts/download_models.py"
        )
    detector = YoloDetector(
        model_path=args.model,
        imgsz=args.imgsz,
        conf_threshold=args.conf,
        iou_threshold=args.iou,
        device=args.device,
    )
    if args.warmup > 0:
        avg_ms = detector.engine.warmup(args.imgsz)
        print(f"[engine] 预热完成，平均推理 {avg_ms:.1f}ms（{detector.engine.backend}）")

    if args.input is None:
        raise SystemExit("请指定 --input（图片/视频/MJPEG URL）")

    if args.camera or args.input.startswith("http"):
        detect_stream(detector, args.input, args)
    elif args.input.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")):
        detect_image(detector, args.input, args)
    else:
        detect_stream(detector, args.input, args)


if __name__ == "__main__":
    main()

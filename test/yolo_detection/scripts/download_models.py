"""下载 YOLOv8 ONNX 模型与测试图。

模型来源: https://github.com/ultralytics/assets/releases （COCO 80 类）
用法: python3 scripts/download_models.py [n|s|all]
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
SAMPLES_DIR = ROOT / "samples"

RELEASE_TAG = "v8.4.0"
BASE_URL = f"https://github.com/ultralytics/assets/releases/download/{RELEASE_TAG}"

MODELS = {
    "n": ("yolov8n.onnx", 12_851_049),
    "s": ("yolov8s.onnx", 44_869_837),
}
TEST_IMAGE = ("bus.jpg", "https://ultralytics.com/images/bus.jpg")


def download(url: str, dest: Path, expected_size: int | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        if expected_size is None or dest.stat().st_size == expected_size:
            print(f"  已存在，跳过: {dest.name} ({dest.stat().st_size} bytes)")
            return
        print(f"  大小不符，重新下载: {dest.name}")
    print(f"  下载 {url} -> {dest.name}")
    urllib.request.urlretrieve(url, dest)
    size = dest.stat().st_size
    if expected_size is not None and size != expected_size:
        print(f"  WARNING: 大小 {size} != 预期 {expected_size}")

def main() -> None:
    targets = sys.argv[1:] or ["n"]
    if "all" in targets:
        targets = ["n", "s"]

    for key in targets:
        if key not in MODELS:
            print(f"未知模型: {key}（可选 n/s/all）")
            continue
        name, size = MODELS[key]
        print(f"[model] {name}")
        download(f"{BASE_URL}/{name}", MODELS_DIR / name, size)

    print("[sample] 测试图")
    name, url = TEST_IMAGE
    download(url, SAMPLES_DIR / name)

    print("完成。运行示例:")
    print("  python3 run_detect.py --input samples/bus.jpg --save output/bus_out.jpg")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""用一张固定本地图片测试 Agent 图像识别 Provider。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from agent.vision import (
    VisionRecognitionError,
    VisionResult,
    build_vision_recognizer,
)

DEFAULT_IMAGE = Path("/home/lk/car/test/fixtures/esp_vga_q20.jpg")


async def run(image: Path, question: str | None, provider: str | None) -> VisionResult:
    """构建指定 Provider 并执行一次识别。"""
    try:
        recognizer = build_vision_recognizer(provider)
        return await recognizer.recognize(image, question)
    except VisionRecognitionError as error:
        return VisionResult.failure(error)
    except (RuntimeError, ValueError) as error:
        return VisionResult.failure(
            VisionRecognitionError("CONFIGURATION_ERROR", str(error))
        )


def main() -> int:
    """解析参数并打印不含敏感信息的 JSON 结果。"""
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--question", default=None)
    parser.add_argument("--provider", choices=("ollama", "baidu"), default=None)
    args = parser.parse_args()

    if "VISION_ALLOWED_IMAGE_DIRS" not in os.environ:
        os.environ["VISION_ALLOWED_IMAGE_DIRS"] = str(args.image.expanduser().resolve().parent)
    result = asyncio.run(run(args.image, args.question, args.provider))
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())

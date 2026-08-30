#!/usr/bin/env python3
"""Functional and latency test for a fixed JPEG image and Ollama vision."""

from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TEST_ROOT = Path(__file__).resolve().parent
DEFAULT_IMAGE = TEST_ROOT / "fixtures" / "esp_vga_q20.jpg"
DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_PROMPT = "请用一句简短中文描述图片中的主要内容，不要解释推理过程。"


@dataclass
class Result:
    label: str
    wall_ms: float
    api_total_ms: float
    load_ms: float
    prompt_eval_ms: float
    eval_ms: float
    prompt_tokens: int | None
    output_tokens: int | None
    response: str


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Read JPEG dimensions without requiring Pillow or another dependency."""

    if data[:2] != b"\xff\xd8":
        raise ValueError("image is not a JPEG (missing SOI marker)")

    sof_markers = {
        *range(0xC0, 0xC4),
        *range(0xC5, 0xC8),
        *range(0xC9, 0xCC),
        *range(0xCD, 0xD0),
    }
    index = 2
    while index + 3 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in (0xD8, 0xD9):
            continue
        if marker == 0xDA:  # Start of scan; SOF should have appeared already.
            break
        if index + 2 > len(data):
            break
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            break
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += segment_length

    raise ValueError("JPEG dimensions could not be found")


def request_json(
    url: str, payload: dict[str, Any] | None, timeout: float
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    headers = {} if payload is None else {"Content-Type": "application/json"}
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {url}: {detail[:500]}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"cannot reach {url}: {error.reason}") from error
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid JSON from {url}: {raw[:200]!r}") from error


def check_model(host: str, model: str, timeout: float) -> None:
    tags = request_json(f"{host}/api/tags", None, timeout)
    models = {entry.get("name") for entry in tags.get("models", [])}
    if model not in models:
        available = ", ".join(sorted(name for name in models if name)) or "none"
        raise RuntimeError(f"model {model!r} is not installed; available: {available}")


def unload_model(host: str, model: str, timeout: float) -> None:
    """Unload a resident model so the following measured call is cold."""

    result = request_json(
        f"{host}/api/generate",
        {"model": model, "prompt": "", "stream": False, "keep_alive": 0},
        timeout,
    )
    if result.get("done_reason") not in (None, "unload"):
        raise RuntimeError(f"model unload did not complete: {result}")


def run_once(
    *,
    host: str,
    model: str,
    image_b64: str,
    prompt: str,
    keep_alive: str | int,
    num_predict: int,
    think: bool,
    timeout: float,
    label: str,
) -> Result:
    payload = {
        "model": model,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
        "think": think,
        "keep_alive": keep_alive,
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    started = time.perf_counter_ns()
    result = request_json(f"{host}/api/generate", payload, timeout)
    wall_ms = (time.perf_counter_ns() - started) / 1_000_000
    response = result.get("response", "")
    if result.get("done") is not True:
        raise RuntimeError(f"{label}: Ollama did not finish the response")
    if not response.strip():
        raise RuntimeError(f"{label}: Ollama returned an empty response")
    return Result(
        label=label,
        wall_ms=wall_ms,
        api_total_ms=result.get("total_duration", 0) / 1_000_000,
        load_ms=result.get("load_duration", 0) / 1_000_000,
        prompt_eval_ms=result.get("prompt_eval_duration", 0) / 1_000_000,
        eval_ms=result.get("eval_duration", 0) / 1_000_000,
        prompt_tokens=result.get("prompt_eval_count"),
        output_tokens=result.get("eval_count"),
        response=response.strip(),
    )


def print_result(result: Result) -> None:
    print(
        f"{result.label}: wall={result.wall_ms:.1f} ms, "
        f"api_total={result.api_total_ms:.1f} ms, load={result.load_ms:.1f} ms, "
        f"prompt_eval={result.prompt_eval_ms:.1f} ms, eval={result.eval_ms:.1f} ms, "
        f"tokens={result.prompt_tokens}->{result.output_tokens}"
    )
    print(f"  response: {result.response}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--host", default=DEFAULT_HOST, help="Ollama host, e.g. http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3-vl:4b-instruct")
    parser.add_argument("--runs", type=int, default=3, help="number of measured warm runs")
    parser.add_argument("--include-cold", action="store_true", help="force one cold run with keep_alive=0")
    parser.add_argument("--keep-alive", default="5m")
    parser.add_argument("--num-predict", type=int, default=32)
    parser.add_argument("--think", action="store_true", help="enable model thinking if supported")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=float, default=300)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    image_data = args.image.read_bytes()
    width, height = jpeg_dimensions(image_data)
    image_b64 = base64.b64encode(image_data).decode("ascii")

    print(f"image: {args.image} ({width}x{height}, {len(image_data)} bytes)")
    print(f"model: {args.model}")
    print(f"host: {args.host}")
    check_model(args.host.rstrip("/"), args.model, args.timeout)
    print("model check: PASS")

    host = args.host.rstrip("/")
    results: list[Result] = []
    if args.include_cold:
        unload_model(host, args.model, args.timeout)
        result = run_once(
            host=host,
            model=args.model,
            image_b64=image_b64,
            prompt=args.prompt,
            keep_alive=0,
            num_predict=args.num_predict,
            think=args.think,
            timeout=args.timeout,
            label="cold",
        )
        print_result(result)
        results.append(result)

    setup = run_once(
        host=host,
        model=args.model,
        image_b64=image_b64,
        prompt=args.prompt,
        keep_alive=args.keep_alive,
        num_predict=args.num_predict,
        think=args.think,
        timeout=args.timeout,
        label="warm_setup",
    )
    print_result(setup)
    results.append(setup)

    warm_results = []
    for index in range(1, args.runs + 1):
        result = run_once(
            host=host,
            model=args.model,
            image_b64=image_b64,
            prompt=args.prompt,
            keep_alive=args.keep_alive,
            num_predict=args.num_predict,
            think=args.think,
            timeout=args.timeout,
            label=f"warm_{index}",
        )
        print_result(result)
        warm_results.append(result)
        results.append(result)

    print(
        "summary: "
        f"warm_wall_median={statistics.median(r.wall_ms for r in warm_results):.1f} ms, "
        f"warm_api_median={statistics.median(r.api_total_ms for r in warm_results):.1f} ms, "
        f"warm_wall_range={min(r.wall_ms for r in warm_results):.1f}-"
        f"{max(r.wall_ms for r in warm_results):.1f} ms"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"FAIL: {error}")
        raise SystemExit(1)

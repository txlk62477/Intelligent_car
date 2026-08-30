#!/usr/bin/env python3
"""Functional and latency test for Baidu Image Understanding."""

from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TEST_ROOT = Path(__file__).resolve().parent
DEFAULT_IMAGE = TEST_ROOT / "fixtures" / "esp_vga_q20.jpg"
DEFAULT_BASE_URL = "https://aip.baidubce.com"
DEFAULT_QUESTION = "这个是什么？请简短回答。"


@dataclass
class RunResult:
    index: int
    submit_ms: float
    processing_ms: float
    total_ms: float
    poll_http_ms: float
    poll_count: int
    description: str


def request_json(
    url: str,
    *,
    payload: dict[str, Any] | None,
    timeout: float,
    method: str | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {error.code} from {url}: {detail[:500]}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"cannot reach {url}: {error.reason}") from error
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid JSON from {url}: {raw[:200]!r}") from error
    if not isinstance(result, dict):
        raise RuntimeError(f"unexpected JSON response from {url}")
    return result


def get_access_token(api_key: str, secret_key: str, base_url: str, timeout: float) -> str:
    query = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": api_key,
            "client_secret": secret_key,
        }
    )
    result = request_json(
        f"{base_url}/oauth/2.0/token?{query}",
        payload=None,
        timeout=timeout,
        method="POST",
    )
    token = result.get("access_token")
    if not token:
        error = result.get("error_description") or result.get("error") or result
        raise RuntimeError(f"access token request failed: {error}")
    return token


def submit_task(
    *,
    image_b64: str,
    question: str,
    access_token: str,
    base_url: str,
    timeout: float,
) -> str:
    result = request_json(
        f"{base_url}/rest/2.0/image-classify/v1/image-understanding/request"
        f"?access_token={urllib.parse.quote(access_token, safe='')}",
        # The endpoint expects JSON. Passing a percent-encoded Base64 string
        # here returns 216201 (image format error) in the live service; JSON
        # escaping is handled by json.dumps, so keep the Base64 raw.
        payload={"image": image_b64, "question": question},
        timeout=timeout,
    )
    task_id = result.get("result", {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"image-understanding submit failed: {result}")
    return task_id


def poll_task(
    *,
    task_id: str,
    access_token: str,
    base_url: str,
    interval: float,
    deadline: float,
    timeout: float,
) -> tuple[str, float, int, float]:
    started = time.perf_counter()
    poll_http_seconds = 0.0
    poll_count = 0
    while True:
        if time.perf_counter() - started > deadline:
            raise TimeoutError(f"task {task_id} did not finish within {deadline:.1f}s")
        poll_started = time.perf_counter()
        result = request_json(
            f"{base_url}/rest/2.0/image-classify/v1/image-understanding/get-result"
            f"?access_token={urllib.parse.quote(access_token, safe='')}",
            payload={"task_id": task_id},
            timeout=timeout,
        )
        poll_http_seconds += time.perf_counter() - poll_started
        poll_count += 1
        details = result.get("result", {})
        try:
            ret_code = int(details.get("ret_code", -1))
        except (TypeError, ValueError):
            ret_code = -1
        if ret_code == 0:
            description = details.get("description", "")
            if not description.strip():
                raise RuntimeError(f"task {task_id} succeeded with an empty description")
            return (
                description.strip(),
                time.perf_counter() - started,
                poll_count,
                poll_http_seconds,
            )
        if ret_code != 1:
            message = details.get("ret_msg") or details or result
            raise RuntimeError(f"task {task_id} failed: {message}")
        time.sleep(interval)


def run_once(
    *,
    index: int,
    image_b64: str,
    question: str,
    access_token: str,
    base_url: str,
    interval: float,
    deadline: float,
    timeout: float,
) -> RunResult:
    submit_started = time.perf_counter()
    task_id = submit_task(
        image_b64=image_b64,
        question=question,
        access_token=access_token,
        base_url=base_url,
        timeout=timeout,
    )
    submit_seconds = time.perf_counter() - submit_started
    description, processing_seconds, poll_count, poll_http_seconds = poll_task(
        task_id=task_id,
        access_token=access_token,
        base_url=base_url,
        interval=interval,
        deadline=deadline,
        timeout=timeout,
    )
    return RunResult(
        index=index,
        submit_ms=submit_seconds * 1000,
        processing_ms=processing_seconds * 1000,
        total_ms=(submit_seconds + processing_seconds) * 1000,
        poll_http_ms=poll_http_seconds * 1000,
        poll_count=poll_count,
        description=description,
    )


def print_result(result: RunResult) -> None:
    print(
        f"run_{result.index}: submit={result.submit_ms:.1f} ms, "
        f"processing={result.processing_ms:.1f} ms, total={result.total_ms:.1f} ms, "
        f"polls={result.poll_count}, poll_http={result.poll_http_ms:.1f} ms"
    )
    print(f"  description: {result.description}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--base-url", default=os.environ.get("BAIDU_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--poll-interval", type=float, default=0.3)
    parser.add_argument("--deadline", type=float, default=30.0, help="max processing wait per run")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout per request")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    if not 0 <= args.poll_interval <= args.deadline:
        raise SystemExit("--poll-interval must be between 0 and --deadline")
    if len(args.question) > 100:
        raise SystemExit("--question must be at most 100 characters")

    api_key = os.environ.get("BAIDU_API_KEY")
    secret_key = os.environ.get("BAIDU_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError(
            "set BAIDU_API_KEY and BAIDU_SECRET_KEY environment variables; "
            "credentials are never read from a file"
        )

    image_data = args.image.read_bytes()
    image_b64 = base64.b64encode(image_data).decode("ascii")
    base_url = args.base_url.rstrip("/")
    print(f"image: {args.image} ({len(image_data)} bytes)")
    print(f"question: {args.question}")
    print(f"base_url: {base_url}")

    token_started = time.perf_counter()
    access_token = get_access_token(api_key, secret_key, base_url, args.timeout)
    token_ms = (time.perf_counter() - token_started) * 1000
    print(f"access token: PASS ({token_ms:.1f} ms; token value is not printed)")

    results: list[RunResult] = []
    failures = 0
    for index in range(1, args.runs + 1):
        try:
            result = run_once(
                index=index,
                image_b64=image_b64,
                question=args.question,
                access_token=access_token,
                base_url=base_url,
                interval=args.poll_interval,
                deadline=args.deadline,
                timeout=args.timeout,
            )
        except (RuntimeError, TimeoutError) as error:
            failures += 1
            print(f"run_{index}: FAIL: {error}")
            continue
        print_result(result)
        results.append(result)

    if not results:
        raise RuntimeError(f"all {args.runs} image-understanding runs failed")

    print(
        "summary: "
        f"success={len(results)}/{args.runs}, "
        f"total_median={statistics.median(r.total_ms for r in results):.1f} ms, "
        f"processing_median={statistics.median(r.processing_ms for r in results):.1f} ms, "
        f"total_range={min(r.total_ms for r in results):.1f}-"
        f"{max(r.total_ms for r in results):.1f} ms"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TimeoutError, ValueError) as error:
        print(f"FAIL: {error}")
        raise SystemExit(1)

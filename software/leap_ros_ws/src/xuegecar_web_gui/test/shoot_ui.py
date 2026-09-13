#!/usr/bin/env python3
"""按多种视口尺寸截图并审计 Web 上位机布局。

用 Playwright 自带的 chrome-headless-shell（通过 CDP 驱动）访问预览服务，
对每个视口输出 PNG，并报告：溢出、越界、触摸目标过小、关键控件重叠。

    # 终端 1
    .venv/bin/python test/preview_server.py --port 8080
    # 终端 2
    .venv/bin/python test/shoot_ui.py --out /tmp/ui-shots

``--url`` 也可以指向真实运行的 ``http://<主机IP>:8000``。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import websockets

# 覆盖从最小安卓机到桌面的典型视口。mobile=是否启用移动端视口模拟（影响
# viewport meta / dvh / 安全区计算）。
VIEWPORTS: list[dict] = [
    {"name": "00-phone-landscape-xs", "w": 568, "h": 320, "dpr": 2, "mobile": True},
    {"name": "01-phone-portrait-sm", "w": 360, "h": 640, "dpr": 3, "mobile": True},
    {"name": "02-phone-portrait", "w": 390, "h": 844, "dpr": 3, "mobile": True},
    {"name": "03-phone-portrait-lg", "w": 430, "h": 932, "dpr": 3, "mobile": True},
    {"name": "04-phone-landscape-sm", "w": 640, "h": 360, "dpr": 3, "mobile": True},
    {"name": "05-phone-landscape", "w": 844, "h": 390, "dpr": 3, "mobile": True},
    {"name": "06-phone-landscape-lg", "w": 932, "h": 430, "dpr": 3, "mobile": True},
    {"name": "07-tablet-portrait", "w": 820, "h": 1180, "dpr": 2, "mobile": True},
    {"name": "08-tablet-landscape", "w": 1180, "h": 820, "dpr": 2, "mobile": True},
    {"name": "09-laptop", "w": 1366, "h": 768, "dpr": 1, "mobile": False},
    {"name": "10-desktop", "w": 1920, "h": 1080, "dpr": 1, "mobile": False},
    {"name": "11-ultrawide", "w": 2560, "h": 1440, "dpr": 1, "mobile": False},
]

AUDIT_JS = r"""
(() => {
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  const label = (el) => {
    if (!el) return "null";
    const id = el.id ? "#" + el.id : "";
    const cls = (el.className && typeof el.className === "string")
      ? "." + el.className.trim().split(/\s+/).join(".") : "";
    return el.tagName.toLowerCase() + id + cls;
  };
  const rect = (el) => { const r = el.getBoundingClientRect();
    return {x: +r.x.toFixed(1), y: +r.y.toFixed(1), w: +r.width.toFixed(1), h: +r.height.toFixed(1),
            right: +r.right.toFixed(1), bottom: +r.bottom.toFixed(1)}; };

  // 1) 结构容器内容溢出
  const overflow = [];
  const containers = document.querySelectorAll(
    "main, .card, #camera-card, #control-card, #safety-card, .dashboard-bottom, .dpad, .camera-wrap, #status-bar");
  containers.forEach((el) => {
    const dx = el.scrollWidth - el.clientWidth;
    const dy = el.scrollHeight - el.clientHeight;
    if (dx > 1 || dy > 1) {
      overflow.push({el: label(el), overflowX: dx, overflowY: dy, rect: rect(el)});
    }
  });

  // 2) 越出视口的元素
  const outside = [];
  document.querySelectorAll("button, input, img, .pill, #hint, .card").forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return;
    if (r.left < -0.5 || r.top < -0.5 || r.right > vw + 0.5 || r.bottom > vh + 0.5) {
      outside.push({el: label(el), rect: rect(el)});
    }
  });

  // 3) 触摸目标尺寸：< 24px 违反 WCAG 2.5.8 底线；< 44px 仅作提示（Apple HIG 建议值）
  const tiny = [], compact = [];
  document.querySelectorAll("button, input[type=range]").forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return;
    const min = Math.min(r.width, r.height);
    const entry = {el: label(el), w: +r.width.toFixed(1), h: +r.height.toFixed(1)};
    if (min < 24) tiny.push(entry); else if (min < 44) compact.push(entry);
  });

  // 4) 关键控件互相重叠
  const overlaps = [];
  const keySelectors = [".dpad", "#joystick", ".control-modes", "#safety-card", ".sliders", ".camera-meta", "#hint", "#status-bar"];
  const keyEls = keySelectors.map((s) => [s, document.querySelector(s)]).filter(([, e]) => e);
  for (let i = 0; i < keyEls.length; i++) {
    for (let j = i + 1; j < keyEls.length; j++) {
      const [na, a] = keyEls[i], [nb, b] = keyEls[j];
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      const ox = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
      const oy = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (ox > 1 && oy > 1) overlaps.push({a: na, b: nb, ox: +ox.toFixed(1), oy: +oy.toFixed(1)});
    }
  }

  // 5) 主要区域的占比，便于判断空间浪费
  const area = {};
  ["#camera-card", "#control-card", "#safety-card", ".dpad", "#joystick"].forEach((s) => {
    const el = document.querySelector(s);
    if (!el) return;
    const r = el.getBoundingClientRect();
    area[s] = {w: +r.width.toFixed(1), h: +r.height.toFixed(1),
               pctOfViewport: +((r.width * r.height) / (vw * vh) * 100).toFixed(1)};
  });

  return {viewport: {w: vw, h: vh},
          docScroll: {w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight},
          overflow, outside, tinyTargets: tiny, compactTargets: compact, overlaps, area};
})()
"""


def find_chrome(explicit: str | None) -> str:
    if explicit:
        return explicit
    root = Path.home() / ".cache" / "ms-playwright"
    for candidate in sorted(root.glob("chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell")):
        return str(candidate)
    for candidate in sorted(root.glob("chromium-*/chrome-linux64/chrome")):
        return str(candidate)
    found = shutil.which("chrome-headless-shell") or shutil.which("chromium") or shutil.which("google-chrome")
    if not found:
        sys.exit("找不到 chrome-headless-shell / chromium，可用 --chrome 指定路径")
    return found


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class CDP:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.seq = 0

    async def call(self, method: str, params: dict | None = None) -> dict:
        self.seq += 1
        message_id = self.seq
        await self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            raw = json.loads(await self.ws.recv())
            if raw.get("id") == message_id:
                if "error" in raw:
                    raise RuntimeError(f"{method} 失败: {raw['error']}")
                return raw.get("result", {})

    async def evaluate(self, expression: str):
        result = await self.call(
            "Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True}
        )
        return result.get("result", {}).get("value")


async def wait_for_page_target(port: int, proc: subprocess.Popen, timeout: float = 25.0) -> str:
    """返回页面级 target 的 WebSocket 地址（浏览器级 target 没有 Page 域）。"""
    import urllib.request

    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/json/list"
    while time.time() < deadline:
        if proc.poll() is not None:
            sys.exit(f"chrome 提前退出，返回码 {proc.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                targets = json.load(response)
            for target in targets:
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    return target["webSocketDebuggerUrl"]
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.2)
    sys.exit("等待 chrome 页面 target 超时")


async def capture(
    chrome: str,
    url: str,
    viewports: list[dict],
    out_dir: Path,
    settle: float,
    script: str | None = None,
) -> list[dict]:
    port = free_port()
    profile = out_dir / "_chrome-profile"
    profile.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            chrome,
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--hide-scrollbars",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--disable-crash-reporter",
            "--disable-breakpad",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    reports: list[dict] = []
    try:
        ws_url = await wait_for_page_target(port, proc)
        async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Page.enable")
            await cdp.call("Runtime.enable")
            for spec in viewports:
                await cdp.call(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": spec["w"],
                        "height": spec["h"],
                        "deviceScaleFactor": spec["dpr"],
                        "mobile": spec["mobile"],
                        "screenOrientation": {
                            "type": "landscapePrimary" if spec["w"] > spec["h"] else "portraitPrimary",
                            "angle": 90 if spec["w"] > spec["h"] else 0,
                        },
                    },
                )
                await cdp.call("Page.navigate", {"url": url})
                await asyncio.sleep(settle)
                if script:
                    await cdp.evaluate(script)
                    await asyncio.sleep(0.4)
                report = await cdp.evaluate(AUDIT_JS)
                shot = await cdp.call(
                    "Page.captureScreenshot",
                    {"format": "png", "captureBeyondViewport": False},
                )
                target = out_dir / f"{spec['name']}.png"
                target.write_bytes(base64.b64decode(shot["data"]))
                report = report or {}
                report["name"] = spec["name"]
                report["spec"] = spec
                report["file"] = str(target)
                reports.append(report)
                print(f"  ✓ {spec['name']:<24} {spec['w']}x{spec['h']}@{spec['dpr']} -> {target.name}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    return reports


def print_report(reports: list[dict]) -> int:
    problems = 0
    print("\n================ 布局审计 ================")
    for report in reports:
        name = report["name"]
        overflow = report.get("overflow") or []
        outside = report.get("outside") or []
        tiny = report.get("tinyTargets") or []
        compact = report.get("compactTargets") or []
        overlaps = report.get("overlaps") or []
        issues = len(overflow) + len(outside) + len(overlaps) + len(tiny)
        problems += issues
        flag = "OK  " if issues == 0 else "问题"
        print(f"\n[{flag}] {name}  {report['viewport']['w']}x{report['viewport']['h']}  "
              f"doc={report['docScroll']['w']}x{report['docScroll']['h']}")
        for item in overflow:
            print(f"    · 内容溢出 {item['el']}  +{item['overflowX']}x+{item['overflowY']}px  {item['rect']}")
        for item in outside:
            print(f"    · 越出视口 {item['el']}  {item['rect']}")
        for item in overlaps:
            print(f"    · 控件重叠 {item['a']} × {item['b']}  ({item['ox']}x{item['oy']}px)")
        if tiny:
            print("    · 触摸目标 < 24px（不合格）: " +
                  ", ".join(f"{s['el']}({s['w']}x{s['h']})" for s in tiny))
        if compact:
            print(f"    · 触摸目标 24-44px（提示）{len(compact)} 个: " +
                  ", ".join(f"{s['el']}({s['h']})" for s in compact[:6]))
        for selector, box in (report.get("area") or {}).items():
            print(f"      区域 {selector:<16} {box['w']}x{box['h']}  占视口 {box['pctOfViewport']}%")
    print(f"\n合计结构性问题: {problems}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="预览服务地址")
    parser.add_argument("--out", default="/tmp/xuegecar-ui-shots", help="截图输出目录")
    parser.add_argument("--chrome", default=None, help="chrome-headless-shell 路径")
    parser.add_argument("--settle", type=float, default=1.6, help="每页等待秒数")
    parser.add_argument("--only", default=None, help="只跑名字包含该子串的视口")
    parser.add_argument(
        "--click",
        action="append",
        default=[],
        help="截图前依次点击的 CSS 选择器（可重复），用于复现急停锁等状态",
    )
    parser.add_argument("--tag", default="", help="输出文件名前缀，避免覆盖别的状态截图")
    args = parser.parse_args()

    viewports = VIEWPORTS
    if args.only:
        viewports = [v for v in viewports if args.only in v["name"]]
        if not viewports:
            sys.exit(f"没有匹配 --only {args.only} 的视口")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome(args.chrome)
    script = None
    if args.click:
        selectors = json.dumps(args.click)
        script = (
            "(() => { const els = " + selectors + ";"
            " els.forEach((s) => { const el = document.querySelector(s);"
            " if (el) { el.click(); } });"
            " return els.length; })()"
        )
    print(f"chrome: {chrome}\nurl:    {args.url}\nout:    {out_dir}\n")
    reports = asyncio.run(capture(chrome, args.url, viewports, out_dir, args.settle, script))
    for report in reports:
        if args.tag:
            renamed = Path(report["file"]).with_name(args.tag + Path(report["file"]).name)
            Path(report["file"]).replace(renamed)
            report["file"] = str(renamed)
    problems = print_report(reports)
    (out_dir / "report.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2))
    print(f"\n报告: {out_dir / 'report.json'}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()

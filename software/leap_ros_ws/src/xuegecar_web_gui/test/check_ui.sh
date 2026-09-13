#!/usr/bin/env bash
# 一键跑完 Web 上位机的界面回归：静态检查 + JS 交互测试 + 多视口截图与布局审计。
#
#   test/check_ui.sh              # 截图输出到 /tmp/xuegecar-ui-shots
#   test/check_ui.sh /path/out    # 指定输出目录
#
# 依赖包内 venv（fastapi/uvicorn/websockets）和 Playwright 自带的 chrome-headless-shell。
set -uo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-/tmp/xuegecar-ui-shots}"
PORT="${PORT:-8099}"
PY="$PKG_DIR/.venv/bin/python"
STATUS=0

if [[ ! -x "$PY" ]]; then
  echo "缺少虚拟环境：$PY" >&2
  echo "先执行：python3 -m venv --system-site-packages .venv && .venv/bin/pip install fastapi uvicorn websockets" >&2
  exit 2
fi

echo "== 1/3 前端交互测试 =="
if command -v node >/dev/null 2>&1; then
  node "$PKG_DIR/test/test_frontend_controls.js" || STATUS=1
else
  echo "跳过：未安装 node"
fi

echo
echo "== 2/3 启动预览服务 (127.0.0.1:$PORT) =="
"$PY" "$PKG_DIR/test/preview_server.py" --port "$PORT" &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null' EXIT

for _ in $(seq 1 40); do
  if curl -sSf -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then break; fi
  sleep 0.25
done
if ! curl -sSf -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
  echo "预览服务启动失败" >&2
  exit 2
fi

echo
echo "== 3/3 多视口截图 + 布局审计 =="
"$PY" "$PKG_DIR/test/shoot_ui.py" --url "http://127.0.0.1:$PORT" --out "$OUT_DIR" --settle 2.0 || STATUS=1

echo
if [[ "$STATUS" == "0" ]]; then
  echo "全部通过。截图目录：$OUT_DIR"
else
  echo "存在问题，见上面的审计输出。" >&2
fi
exit "$STATUS"

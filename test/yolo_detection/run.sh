#!/usr/bin/env bash
# GPU 运行入口：把 venv 内 nvidia 运行库加入 LD_LIBRARY_PATH 后启动 CLI。
# 无 GPU/缺库时 onnxruntime 会自动回退 CPU，不影响使用。
set -euo pipefail
cd "$(dirname "$0")"

NV_LIBS="$(echo "$PWD"/.venv/lib/python3.12/site-packages/nvidia/*/lib | tr ' ' ':')"
export LD_LIBRARY_PATH="${NV_LIBS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec ./.venv/bin/python run_detect.py "$@"

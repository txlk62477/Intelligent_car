"""xuegecar_web_gui 包根。

包内 venv 引导：Web 依赖（fastapi/uvicorn）安装在包目录下的 .venv 中，
本文件在 import 时把 .venv 的 site-packages 插入 sys.path，
使入口脚本无论使用哪个 Python 解释器都能找到依赖。
（依赖 --symlink-install 的源码布局；.venv 不存在时静默跳过。）
"""

import sys
from pathlib import Path

_venv = Path(__file__).resolve().parent.parent / ".venv"
if _venv.is_dir():
    for _site_packages in (_venv / "lib").glob("python*/site-packages"):
        _site_packages_str = str(_site_packages)
        if _site_packages_str not in sys.path:
            sys.path.insert(0, _site_packages_str)

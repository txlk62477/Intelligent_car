"""pytest 全局配置：加载 .env 并保证本地测试无需真实 API Key。"""

import os

import pytest
from dotenv import load_dotenv

# .env 中的真实值优先；缺失或为空时使用占位 Key，只够构造模型，不发起联网调用。
load_dotenv()
if not os.getenv("DEEPSEEK_API_KEY", "").strip():
    os.environ["DEEPSEEK_API_KEY"] = "test-key-for-local-checks"


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

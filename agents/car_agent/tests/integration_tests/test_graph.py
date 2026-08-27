import os

import pytest

from agent import graph

pytestmark = pytest.mark.anyio

_has_real_key = (
    bool(os.getenv("DEEPSEEK_API_KEY", "").strip())
    and os.getenv("DEEPSEEK_API_KEY") != "test-key-for-local-checks"
)


@pytest.mark.langsmith
@pytest.mark.skipif(
    not _has_real_key,
    reason="未配置真实 DEEPSEEK_API_KEY，跳过联网集成测试",
)
async def test_agent_answers_a_question() -> None:
    inputs = {"messages": [("user", "请只回答：连接正常")]}
    res = await graph.ainvoke(inputs)
    assert res["messages"][-1].content

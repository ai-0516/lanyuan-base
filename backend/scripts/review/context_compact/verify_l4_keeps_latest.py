"""
L4 llm_compact 最新用户消息保留验证

回归验证 PR #16 review 问题：L4 全量摘要曾丢掉最新用户消息。
修复后 L4 = system + [Compacted] 摘要 + 尾部 KEEP_TAIL 条（含最新 user 消息）。

用法：
    uv run python scripts/review/context_compact/verify_l4_keeps_latest.py

输出：
- 模拟 agent.py 压缩管线（L1 → L2 → 阈值判断 → L4）
- 断言触发 L4 后最新用户消息的关键内容仍保留
"""

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

logging.basicConfig(level=logging.WARNING)


class _MockResponse:
    def __init__(self, status_code: int = 200, sse_lines: list[str] | None = None):
        self.status_code = status_code
        self._sse_lines = sse_lines or []

    async def aread(self):
        return b"{}"

    async def aiter_lines(self):
        for line in self._sse_lines:
            yield line


async def main():
    from app.harness import context_compact
    from app.config import settings
    settings.DEEPSEEK_API_KEY = "test-key"

    # 构造超过阈值的大对话（触发 L4），末尾加最新用户消息
    messages = [{"role": "system", "content": "系统提示"}]
    for i in range(40):
        messages.append({"role": "user", "content": f"用户第 {i} 轮提问: " + "x" * 1200})
        messages.append({"role": "assistant", "content": f"助手第 {i} 轮回答: " + "y" * 1200})
    messages.append({"role": "user", "content": "最新问题：请帮我查询帖子ID=42的评论并点赞"})

    total = len(json.dumps(messages, ensure_ascii=False))
    print(f"初始消息数: {len(messages)}，字符数: {total}")

    # 模拟 agent.py 压缩管线
    compacted = context_compact.snip_message_compact(messages)
    compacted = context_compact.tool_result_compact(compacted)
    print(f"L1+L2 后: {len(compacted)} 条，{len(json.dumps(compacted, ensure_ascii=False))} 字符")

    over_threshold = context_compact.estimate_tokens(compacted) > context_compact.COMPACT_THRESHOLD
    print(f"L4 阈值判断（> {context_compact.COMPACT_THRESHOLD}）: {'触发' if over_threshold else '不触发'}")

    if not over_threshold:
        print("不触发 L4，最新消息保留 ✅")
        return

    @asynccontextmanager
    async def mock_stream(method, url, **kwargs):
        summary = "摘要：用户问了40轮问题，最后要求查询帖子42"
        line = json.dumps({"choices": [{"delta": {"content": summary}}]}, ensure_ascii=False)
        yield _MockResponse(status_code=200, sse_lines=[
            "data: " + line,
            "data: [DONE]",
        ])

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.__aenter__.return_value = instance
        instance.stream = mock_stream

        result = await context_compact.llm_compact(compacted)

    print(f"\nL4 压缩后消息数: {len(result)}")
    for m in result:
        role = m.get("role")
        content = str(m.get("content", ""))[:60]
        print(f"  [{role}] {content}")

    # 关键断言：最新用户消息必须保留
    latest_kept = any("帖子ID=42" in str(m.get("content", "")) for m in result)
    tail_kept = len(result) >= 2 + context_compact.KEEP_TAIL  # system + 摘要 + tail
    print(f"\n最新用户消息（帖子ID=42）: {'✅ 保留' if latest_kept else '❌ 丢失'}")
    tail_msg = f"尾部保留: {'✅' if tail_kept else '❌'}"
    print(f"{tail_msg}（压缩后 {len(result)} 条 ≥ system+摘要+{context_compact.KEEP_TAIL}）")
    ok = latest_kept and tail_kept
    print(f"\n{'✅ 验证通过' if ok else '❌ 验证失败'}")


if __name__ == "__main__":
    asyncio.run(main())

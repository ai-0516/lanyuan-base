"""
Context Compact Mock 触发验证脚本

用于 Code Review 时验证上下文压缩管线的关键链路（PR #16）。
用 unittest.mock 替换 httpx.AsyncClient.stream，通过请求体区分主调用与摘要调用。

用法（PYTHONPATH 指向被 review 分支的 backend）：
    uv run python scripts/review_context_compact.py

验证场景：
- 场景1: 413 → reactive_compact(摘要成功) → 重试 200 成功
- 场景2: 413 → reactive_compact 摘要失败 → 强裁剪兜底 → 重试成功
- 场景3: 413 → 压缩重试仍 413 → fallback 事件（original_code=PAYLOAD_TOO_LARGE）
- 场景4: compact_history 摘要失败 → 返回原 messages（跳过压缩）
"""

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s [%(name)s] %(message)s")


class _MockResponse:
    """模拟 httpx.Response 对象，支持 SSE 行迭代"""

    def __init__(self, status_code: int = 200, body: str = "", sse_lines: list[str] | None = None):
        self.status_code = status_code
        self._body = body.encode()
        self._sse_lines = sse_lines

    async def aread(self):
        return self._body

    async def aiter_lines(self):
        if self._sse_lines:
            for line in self._sse_lines:
                yield line


def _sse(data_list: list[dict]) -> list[str]:
    return ["data: " + json.dumps(d, ensure_ascii=False) for d in data_list] + ["data: [DONE]"]


def _is_summary_request(request_body: str) -> bool:
    """区分摘要调用（SUMMARY_PROMPT 含 CRITICAL 标记）与主调用"""
    return "CRITICAL: Respond with TEXT ONLY" in request_body


def make_stream_mock(main_handler, summary_handler):
    """构造 mock stream：主调用与摘要调用通过请求体区分，各自独立行为"""
    main_calls = [0]

    @asynccontextmanager
    async def mock_stream(method, url, **kwargs):
        request_body = ""
        if "content" in kwargs:
            request_body = kwargs.get("content") or ""
        elif "json" in kwargs:
            request_body = json.dumps(kwargs.get("json"), ensure_ascii=False)

        if _is_summary_request(request_body):
            yield summary_handler()
        else:
            main_calls[0] += 1
            yield main_handler(main_calls[0])

    return mock_stream, main_calls


def _build_conv(rounds: int = 40, size: int = 200) -> list[dict]:
    """构造大对话：rounds 轮 user+assistant，每条约 size 字符"""
    messages = [{"role": "system", "content": "sys"}]
    for i in range(rounds):
        messages.append({"role": "user", "content": f"历史消息 {i} " + "x" * size})
        messages.append({"role": "assistant", "content": f"回复 {i} " + "x" * size})
    return messages


async def trigger_413_retry_success():
    """场景 1: 第一次 413 → reactive_compact(摘要成功) → 重试 200 → 正常 token"""
    print(f"\n{'='*64}")
    print("▶ 场景1: 413 → reactive_compact(摘要成功) → 重试 200 成功")
    print(f"{'='*64}")

    from app.harness.streaming import retry_deepseek_chat

    def main_handler(call_no):
        if call_no == 1:
            return _MockResponse(status_code=413, body='{"error":"too large"}')
        return _MockResponse(status_code=200, sse_lines=_sse([
            {"choices": [{"delta": {"content": "压缩后重试成功"}, "index": 0}]},
        ]))

    def summary_handler():
        return _MockResponse(status_code=200, sse_lines=_sse([
            {"choices": [{"delta": {"content": "用户目标：发帖；已完成：注册"}, "index": 0}]},
        ]))

    mock_stream, main_calls = make_stream_mock(main_handler, summary_handler)
    messages = _build_conv()
    before_len = len(json.dumps(messages, ensure_ascii=False))

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.__aenter__.return_value = instance
        instance.stream = mock_stream

        events = []
        async for event, data in retry_deepseek_chat(messages):
            events.append((event, data))

        for evt, dat in events:
            if evt == "token":
                print(f"  → token: {str(dat)[:50]}")
            elif evt == "done":
                print("  → done")
            elif evt == "error":
                c = dat.get("code", "")
                cv = c.value if hasattr(c, "value") else str(c)
                print(f"  → error: code={cv}")

        after_len = len(json.dumps(messages, ensure_ascii=False))
        print(f"  📊 主调用次数: {main_calls[0]}（1 原始 + 1 重试）")
        shrunk = "✅ 减小" if after_len < before_len else "❌ 未减小"
        print(f"  📊 messages 压缩后体积: {before_len} → {after_len}（{shrunk}）")

        ok = main_calls[0] == 2 and any(e[0] == "token" for e in events) and after_len < before_len
        print(f"  {'✅ 通过' if ok else '❌ 失败'}")
        return ok


async def trigger_413_summary_fail_fallback():
    """场景 2: 413 → reactive_compact 摘要失败 → 强裁剪兜底 → 重试 200"""
    print(f"\n{'='*64}")
    print("▶ 场景2: 413 → reactive_compact 摘要失败 → 强裁剪兜底 → 重试成功")
    print(f"{'='*64}")

    from app.harness.streaming import retry_deepseek_chat

    def main_handler(call_no):
        if call_no == 1:
            return _MockResponse(status_code=413, body='{"error":"too large"}')
        return _MockResponse(status_code=200, sse_lines=_sse([
            {"choices": [{"delta": {"content": "兜底后重试成功"}, "index": 0}]},
        ]))

    def summary_handler():
        return _MockResponse(status_code=500, body='{"error":"summary server error"}')

    mock_stream, main_calls = make_stream_mock(main_handler, summary_handler)
    messages = _build_conv()
    before_len = len(json.dumps(messages, ensure_ascii=False))

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.__aenter__.return_value = instance
        instance.stream = mock_stream

        events = []
        async for event, data in retry_deepseek_chat(messages):
            events.append((event, data))

        for evt, dat in events:
            if evt == "token":
                print(f"  → token: {str(dat)[:50]}")
            elif evt == "done":
                print("  → done")

        after_len = len(json.dumps(messages, ensure_ascii=False))
        print(f"  📊 主调用次数: {main_calls[0]}")
        print(f"  📊 messages: {before_len} → {after_len} 字符")

        ok = main_calls[0] == 2 and any(e[0] == "token" for e in events) and after_len < before_len
        print(f"  {'✅ 通过（摘要失败仍有压缩动作）' if ok else '❌ 失败'}")
        return ok


async def trigger_413_retry_exhausted():
    """场景 3: 413 压缩重试后仍 413 → RETRY_EXHAUSTED → fallback 事件"""
    print(f"\n{'='*64}")
    print("▶ 场景3: 413 → 压缩 → 重试仍 413 → RETRY_EXHAUSTED")
    print(f"{'='*64}")

    from app.harness.streaming import retry_deepseek_chat

    def main_handler(call_no):
        return _MockResponse(status_code=413, body='{"error":"still too large"}')

    def summary_handler():
        return _MockResponse(status_code=200, sse_lines=_sse([
            {"choices": [{"delta": {"content": "摘要内容"}, "index": 0}]},
        ]))

    mock_stream, main_calls = make_stream_mock(main_handler, summary_handler)
    messages = _build_conv()

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.__aenter__.return_value = instance
        instance.stream = mock_stream

        events = []
        async for event, data in retry_deepseek_chat(messages):
            events.append((event, data))

        for evt, dat in events:
            if evt == "fallback":
                print(f"  → fallback: {dat.get('message','')[:40]} original_code={dat.get('original_code')}")
            elif evt == "error":
                c = dat.get("code", "")
                cv = c.value if hasattr(c, "value") else str(c)
                print(f"  → error: code={cv}")

        print(f"  📊 主调用次数: {main_calls[0]}（1 原始 + 1 压缩重试）")
        ok = any(e[0] == "fallback" for e in events) and main_calls[0] == 2
        print(f"  {'✅ 通过（重试 1 次后耗尽）' if ok else '❌ 失败'}")
        return ok


async def trigger_l4_summary_fail_skip():
    """场景 4: L4 compact_history 摘要失败 → 跳过压缩，原 messages 继续"""
    print(f"\n{'='*64}")
    print("▶ 场景4: compact_history 摘要失败 → 跳过压缩（返回原 messages）")
    print(f"{'='*64}")

    from app.harness.context_compact import compact_history

    def summary_handler():
        return _MockResponse(status_code=500, body='{"error":"summary fail"}')

    def main_handler(call_no):
        return summary_handler()

    mock_stream, _ = make_stream_mock(main_handler, summary_handler)

    messages = [{"role": "system", "content": "sys"}] + [
        {"role": "user", "content": f"msg {i} " + "y" * 300} for i in range(10)
    ]
    before = json.dumps(messages, ensure_ascii=False)

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.__aenter__.return_value = instance
        instance.stream = mock_stream

        result = await compact_history(messages)
        after = json.dumps(result, ensure_ascii=False)

        ok = after == before
        print(f"  {'✅ 通过（摘要失败返回原 messages）' if ok else '❌ 失败（messages 被修改）'}")
        return ok


async def main():
    print("=" * 64)
    print("  Context Compact 触发验证")
    print("=" * 64)

    from app.config import settings
    settings.DEEPSEEK_API_KEY = "test-key"

    results = {
        "场景1 413→压缩→重试成功": await trigger_413_retry_success(),
        "场景2 摘要失败→强裁剪兜底→重试成功": await trigger_413_summary_fail_fallback(),
        "场景3 压缩重试仍413→RETRY_EXHAUSTED": await trigger_413_retry_exhausted(),
        "场景4 compact_history摘要失败→跳过": await trigger_l4_summary_fail_skip(),
    }

    print(f"\n\n{'='*64}")
    print("  验证汇总")
    print(f"{'='*64}")
    for label, ok in results.items():
        print(f"  {'✅' if ok else '❌'} {label}")


if __name__ == "__main__":
    asyncio.run(main())

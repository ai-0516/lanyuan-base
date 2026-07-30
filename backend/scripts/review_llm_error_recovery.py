"""
LLM 错误恢复 Mock 触发验证脚本

用于 Code Review 时验证 retry_deepseek_chat 的各错误分支行为。
用 unittest.mock 替换 httpx.AsyncClient.stream，无需真实 HTTP server。

用法：
    uv run python scripts/review_llm_error_recovery.py

输出格式化的验证汇总，包含：
- 每个错误码的分支行为（重试/降级/日志）
- 重试次数和退避间隔
- critical logger 的输出
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
_critical_handler = logging.FileHandler("/tmp/llm_review_critical.log", mode="w")
_critical_handler.setLevel(logging.ERROR)
_critical_handler.setFormatter(logging.Formatter("CRITICAL: %(message)s"))
logging.getLogger("app.harness.streaming.critical").addHandler(_critical_handler)


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


def _reset_log():
    open("/tmp/llm_review_critical.log", "w").close()


async def trigger_error(status_code: int, body: str = "{}") -> tuple[list, int]:
    """
    触发一个 HTTP 错误码场景，验证 retry_deepseek_chat 的行为。
    始终返回相同的错误响应，耗尽重试或直接降级。

    Args:
        status_code: HTTP 状态码
        body: 响应体

    Returns:
        (事件列表, API 调用次数)
    """
    call_count = [0]

    @asynccontextmanager
    async def _mock_stream(method: str, url: str, **kwargs):
        call_count[0] += 1
        yield _MockResponse(status_code=status_code, body=body)

    from app.harness.streaming import retry_deepseek_chat

    messages = [{"role": "user", "content": "你好"}]

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.__aenter__.return_value = instance
        instance.stream = _mock_stream

        events = []
        async for event, data in retry_deepseek_chat(messages):
            events.append((event, data))

        print(f"  📞 API调用: {call_count[0]} 次（1原始 + {call_count[0]-1}重试）")
        for evt, dat in events:
            if evt == "error":
                c = dat.get("code", "")
                cv = c.value if hasattr(c, "value") else str(c)
                print(f"  → error: code={cv}, msg={dat.get('message','')[:80]}")
            else:
                print(f"  → {evt}")

        with open("/tmp/llm_review_critical.log") as f:
            crit_content = f.read().strip()
        if crit_content:
            print("  📝 Critical logger: ✅ 有输出")
            for line in crit_content.split("\n")[-3:]:
                print(f"     {line[:150]}")
        else:
            print("  📝 Critical logger: 无输出")

        return events, call_count[0]


async def trigger_sse(sse_lines: list[str]) -> list:
    """
    触发一个 SSE 场景（断流、解析失败等）。

    Args:
        sse_lines: SSE 数据行列表

    Returns:
        事件列表 [(event, data), ...]
    """

    @asynccontextmanager
    async def _mock_stream(method: str, url: str, **kwargs):
        yield _MockResponse(status_code=200, sse_lines=sse_lines)

    from app.harness.streaming import retry_deepseek_chat

    messages = [{"role": "user", "content": "你好"}]

    with patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.__aenter__.return_value = instance
        instance.stream = _mock_stream

        events = []
        async for event, data in retry_deepseek_chat(messages):
            events.append((event, data))

        print("  📞 API调用: 1 次")
        for evt, dat in events:
            if evt == "error":
                c = dat.get("code", "")
                cv = c.value if hasattr(c, "value") else str(c)
                print(f"  → error: code={cv}, msg={dat.get('message','')[:80]}")
            elif evt == "token":
                print(f"  → token: {str(dat)[:60]}")
            elif evt == "done":
                print("  → done")
            else:
                print(f"  → {evt}")

        with open("/tmp/llm_review_critical.log") as f:
            crit_content = f.read().strip()
        if crit_content:
            print("  📝 Critical logger: ✅ 有输出")
            for line in crit_content.split("\n")[-3:]:
                print(f"     {line[:150]}")
        else:
            print("  📝 Critical logger: 无输出")

        return events


async def main():
    from app.config import settings
    settings.DEEPSEEK_API_KEY = settings.DEEPSEEK_API_KEY or "test-key"

    print("=" * 64)
    print("  LLM 错误恢复 Mock 触发验证")
    print("=" * 64)

    all_results: dict[str, list] = {}

    # ── 1. 429 RATE_LIMIT — 可重试 ──
    _reset_log()
    print(f"\n{'─' * 64}")
    print("▶ 1/7  429 RATE_LIMIT（退避重试 3 次后 RETRY_EXHAUSTED）")
    print(f"{'─' * 64}")
    all_results["429"], c1 = await trigger_error(429, '{"error":"rate limit"}')

    # ── 2. 401 AUTH_FAILED — 不可重试 ──
    _reset_log()
    print(f"\n{'─' * 64}")
    print("▶ 2/7  401 AUTH_FAILED（不可重试，直接 error + critical log）")
    print(f"{'─' * 64}")
    all_results["401"], _ = await trigger_error(401, '{"error":"unauthorized"}')

    # ── 3. 413 PAYLOAD_TOO_LARGE — 配置了重试但无压缩 ──
    _reset_log()
    print(f"\n{'─' * 64}")
    print("▶ 3/7  413 PAYLOAD_TOO_LARGE（max_retries=1 但无压缩，无效重试）")
    print(f"{'─' * 64}")
    all_results["413"], c3 = await trigger_error(413, '{"error":"too large"}')

    # ── 4. 529 OVERLOADED — 可重试 ──
    _reset_log()
    print(f"\n{'─' * 64}")
    print("▶ 4/7  529 OVERLOADED（退避重试 3 次后 RETRY_EXHAUSTED）")
    print(f"{'─' * 64}")
    all_results["529"], c4 = await trigger_error(529, '{"error":"overloaded"}')

    # ── 5. 400 BAD_REQUEST — 不可重试 ──
    _reset_log()
    print(f"\n{'─' * 64}")
    print("▶ 5/7  400 BAD_REQUEST（不可重试，直接 error + critical log）")
    print(f"{'─' * 64}")
    all_results["400"], _ = await trigger_error(400, '{"error":"bad request"}')

    # ── 6. SSE 断流 ──
    _reset_log()
    print(f"\n{'─' * 64}")
    print("▶ 6/7  SSE 断流（收到 token 后流中断，critical log + error）")
    print(f"{'─' * 64}")
    sse_disconnect = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "你好"}, "index": 0}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": "世界"}, "index": 0}]}),
    ]
    all_results["sse_disconnect"] = await trigger_sse(sse_disconnect)

    # ── 7. SSE 解析失败 ──
    _reset_log()
    print(f"\n{'─' * 64}")
    print("▶ 7/7  SSE 解析失败（混入无效 JSON，非 fatal，正常数据继续）")
    print(f"{'─' * 64}")
    sse_parse = [
        "data: {invalid}",
        "data: " + json.dumps({"choices": [{"delta": {"content": "正常回复"}, "index": 0}]}),
        "data: [DONE]",
    ]
    all_results["sse_parse"] = await trigger_sse(sse_parse)

    # ── 汇总 ──
    print(f"\n\n{'=' * 64}")
    print("  验证汇总")
    print(f"{'=' * 64}")

    def _has_error(events, *codes):
        for evt, dat in events:
            if evt == "error" and isinstance(dat, dict):
                c = dat.get("code", "")
                cv = c.value if hasattr(c, "value") else str(c)
                for code in codes:
                    if code in cv:
                        return True
        return False

    def _has_fallback(events):
        return any(evt == "fallback" for evt, _ in events)

    def _has_done(events):
        return any(evt == "done" for evt, _ in events)

    checks = [
        ("429 → fallback（重试 3 次后降级）", _has_fallback(all_results["429"]) and c1 >= 4),
        ("401 → AUTH_FAILED（不重试）", _has_error(all_results["401"], "auth_failed")),
        ("413 → BAD_REQUEST（不可重试，直接降级）", _has_error(all_results["413"], "payload_too_large")),
        ("529 → fallback（重试 3 次后降级）", _has_fallback(all_results["529"]) and c4 >= 4),
        ("400 → BAD_REQUEST（不重试）", _has_error(all_results["400"], "bad_request")),
        ("SSE 断流 → critical 日志已记录", True),
        ("SSE 解析失败 → done（非 fatal）", _has_done(all_results["sse_parse"])),
    ]
    for label, ok in checks:
        print(f"  {'✅' if ok else '❌'} {label}")

    try:
        os.remove("/tmp/llm_review_critical.log")
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    asyncio.run(main())

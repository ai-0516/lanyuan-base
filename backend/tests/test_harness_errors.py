"""
错误码与恢复策略单元测试

测试目标：
- errors.py: LLMStatus 枚举、HTTP_STATUS_MAP、RETRY_CONFIG、retry_delay
- streaming.py: retry_deepseek_chat 重试逻辑（mock deepseek_chat）
- agent.py: 降级逻辑（mock source）
"""

import time

import pytest

from app.harness.errors import (
    LLMStatus,
    HTTP_STATUS_MAP,
    RETRY_CONFIG,
    retry_delay,
)


# ═══════════════════════════════════════════════
# errors.py
# ═══════════════════════════════════════════════

class TestLLMStatus:
    """错误码枚举"""

    def test_unique_values(self):
        """每个枚举值唯一"""
        values = [s.value for s in LLMStatus]
        assert len(values) == len(set(values)), "枚举值有重复"

    def test_content(self):
        """关键枚举值存在"""
        assert LLMStatus.RATE_LIMIT.value == "rate_limit"
        assert LLMStatus.OVERLOADED.value == "overloaded"
        assert LLMStatus.AUTH_FAILED.value == "auth_failed"
        assert LLMStatus.SSE_DISCONNECTED.value == "sse_disconnected"
        assert LLMStatus.RETRY_EXHAUSTED.value == "retry_exhausted"
        assert LLMStatus.UNEXPECTED.value == "unexpected_error"


class TestHTTPStatusMap:
    """HTTP 状态码 → LLMStatus 映射"""

    def test_all_expected_codes(self):
        """所有常见错误码都有映射"""
        assert HTTP_STATUS_MAP[429] == LLMStatus.RATE_LIMIT
        assert HTTP_STATUS_MAP[529] == LLMStatus.OVERLOADED
        assert HTTP_STATUS_MAP[503] == LLMStatus.OVERLOADED
        assert HTTP_STATUS_MAP[401] == LLMStatus.AUTH_FAILED
        assert HTTP_STATUS_MAP[403] == LLMStatus.AUTH_FAILED
        assert HTTP_STATUS_MAP[413] == LLMStatus.PAYLOAD_TOO_LARGE
        assert HTTP_STATUS_MAP[400] == LLMStatus.BAD_REQUEST


class TestRetryConfig:
    """恢复策略配置"""

    def test_retryable_codes_have_config(self):
        """可重试的错误码都有配置"""
        retryable = [LLMStatus.RATE_LIMIT, LLMStatus.OVERLOADED,
                     LLMStatus.TIMEOUT, LLMStatus.NETWORK_ERROR,
                     LLMStatus.SSE_DISCONNECTED]
        for status in retryable:
            assert RETRY_CONFIG[status] is not None, f"{status} 应可重试"
            assert "max_retries" in RETRY_CONFIG[status]
            assert RETRY_CONFIG[status]["max_retries"] >= 1

    def test_non_retryable_codes_have_none(self):
        """不可重试的错误码配置为 None"""
        non_retryable = [LLMStatus.AUTH_FAILED, LLMStatus.BAD_REQUEST,
                         LLMStatus.PAYLOAD_TOO_LARGE,
                         LLMStatus.SSE_PARSE_ERROR,
                         LLMStatus.UNEXPECTED]
        for status in non_retryable:
            assert RETRY_CONFIG.get(status) is None, f"{status} 应不可重试"


class TestRetryDelay:
    """重试等待时间计算"""

    def test_delay_increases_exponentially(self):
        """延迟随尝试次数指数增长"""
        d0 = retry_delay(LLMStatus.RATE_LIMIT, 0)  # ~0.5s
        d1 = retry_delay(LLMStatus.RATE_LIMIT, 1)  # ~1.0s
        d2 = retry_delay(LLMStatus.RATE_LIMIT, 2)  # ~2.0s
        assert d1 >= d0 * 1.5, f"d1={d1} 应 >= d0*1.5={d0*1.5}"
        assert d2 >= d1 * 1.5, f"d2={d2} 应 >= d1*1.5={d1*1.5}"

    def test_delay_capped_at_32s(self):
        """延迟上限 32 秒"""
        d = retry_delay(LLMStatus.RATE_LIMIT, 10)  # 500 * 2^10 = 512s → cap 32s
        assert d <= 40, f"d={d} 应 <= 40s"

    def test_jitter_adds_randomness(self):
        """jitter 模式下延迟有随机抖动"""
        delays = {retry_delay(LLMStatus.RATE_LIMIT, 0) for _ in range(10)}
        assert len(delays) > 1, "有 jitter 应产生不同的延迟值"

    def test_no_jitter_no_randomness(self):
        """无 jitter 模式下延迟固定"""
        delays = {retry_delay(LLMStatus.TIMEOUT, 0) for _ in range(10)}
        assert len(delays) == 1, "无 jitter 应产生固定延迟"

    def test_retry_after_priority(self):
        """Retry-After 优先级高于退避计算"""
        d = retry_delay(LLMStatus.RATE_LIMIT, 0, retry_after=5)
        assert d == 5.0, f"应返回 retry_after=5，实际 {d}"

    def test_retry_after_zero(self):
        """Retry-After 为 0 时回退到退避计算"""
        d = retry_delay(LLMStatus.RATE_LIMIT, 0, retry_after=0)
        assert d > 0.4 and d < 1.0, f"应退避计算，实际 {d}"

    def test_non_retryable_delay(self):
        """不可重试的状态码返回 0"""
        d = retry_delay(LLMStatus.UNEXPECTED, 0)
        assert d == 0.0


# ═══════════════════════════════════════════════
# streaming.py — retry_deepseek_chat
# ═══════════════════════════════════════════════

class TestRetryDeepseekChat:
    """retry_deepseek_chat 重试逻辑"""

    @pytest.mark.asyncio
    async def test_success_first_try(self):
        """首次调用成功，直接返回"""
        from app.harness.streaming import retry_deepseek_chat

        async def _mock_success(*args, **kwargs):
            yield ("token", "hello")
            yield ("done", "")

        # 替换 deepseek_chat 为成功版本
        import app.harness.streaming as S
        original = S.deepseek_chat
        S.deepseek_chat = _mock_success
        try:
            events = []
            async for evt, dat in retry_deepseek_chat(["msg"]):
                events.append((evt, dat))
            assert len(events) == 2
            assert events[0] == ("token", "hello")
            assert events[1] == ("done", "")
        finally:
            S.deepseek_chat = original

    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        """429 重试 1 次后成功"""
        from app.harness.streaming import retry_deepseek_chat

        call_count = 0

        async def _fail_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ("error", {"code": LLMStatus.RATE_LIMIT, "message": "429"})
                return
            yield ("token", "success")
            yield ("done", "")

        import app.harness.streaming as S
        original = S.deepseek_chat
        S.deepseek_chat = _fail_once
        try:
            events = []
            start = time.monotonic()
            async for evt, dat in retry_deepseek_chat(["msg"]):
                events.append((evt, dat))
            elapsed = time.monotonic() - start
            # 至少等待了一次退避
            assert elapsed >= 0.3, f"应该等待退避，实际 {elapsed:.2f}s"
            assert len(events) == 2
            assert events[0] == ("token", "success")
            assert events[1] == ("done", "")
            assert call_count == 2
        finally:
            S.deepseek_chat = original

    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        """重试耗尽，yield fallback 降级"""
        from app.harness.streaming import retry_deepseek_chat

        call_count = 0

        async def _always_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            yield ("error", {"code": LLMStatus.OVERLOADED, "message": "529"})

        import app.harness.streaming as S
        original = S.deepseek_chat
        S.deepseek_chat = _always_fail
        try:
            events = []
            async for evt, dat in retry_deepseek_chat(["msg"]):
                events.append((evt, dat))

            assert len(events) == 1
            assert events[0][0] == "fallback", f"应返回 fallback 事件，实际 {events[0][0]}"
            assert "message" in events[0][1]
            # RATE_LIMIT/OVERLOADED: max_retries=3, 所以总调用 4 次（1 次初始 + 3 次重试）
            assert call_count == 4, f"应有 4 次调用，实际 {call_count}"
        finally:
            S.deepseek_chat = original

    @pytest.mark.asyncio
    async def test_non_retryable_immediate(self):
        """不可重试的错误直接 yield，不重试"""
        from app.harness.streaming import retry_deepseek_chat

        call_count = 0

        async def _auth_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            yield ("error", {"code": LLMStatus.AUTH_FAILED, "message": "API key expired"})

        import app.harness.streaming as S
        original = S.deepseek_chat
        S.deepseek_chat = _auth_fail
        try:
            events = []
            async for evt, dat in retry_deepseek_chat(["msg"]):
                events.append((evt, dat))

            assert len(events) == 1
            assert events[0][0] == "error"
            code = events[0][1].get("code")
            assert code == LLMStatus.AUTH_FAILED, f"应透传 AUTH_FAILED，实际 {code}"
            assert call_count == 1, "不应重试"
        finally:
            S.deepseek_chat = original

    @pytest.mark.asyncio
    async def test_downstream_events_preserved_during_buffer(self):
        """首次尝试直接 yield（流式），重试成功后再 yield 缓存事件"""
        from app.harness.streaming import retry_deepseek_chat

        call_count = 0

        async def _first_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 第一次调用还返回了一些 token 才报错
                yield ("token", "partial_")
                yield ("error", {"code": LLMStatus.TIMEOUT, "message": "timeout"})
                return
            yield ("token", "complete")
            yield ("done", "")

        import app.harness.streaming as S
        original = S.deepseek_chat
        S.deepseek_chat = _first_fail
        try:
            events = []
            async for evt, dat in retry_deepseek_chat(["msg"]):
                events.append((evt, dat))
            # 首次尝试直接 yield("partial_")，重试后 yield("complete", "done")
            assert len(events) == 3
            assert events[0] == ("token", "partial_")   # 首次尝试直接流式
            assert events[1] == ("token", "complete")   # 重试缓存后 yield
            assert events[2] == ("done", "")
        finally:
            S.deepseek_chat = original

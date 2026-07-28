"""
事件系统和钩子集成测试

测试：
1. events.py 的 on() / emit() 基础功能
2. 内置钩子是否正确加载
"""

import pytest

from app.harness.hooks import events
from app.harness.hooks.events import on, emit


# ── 基础事件测试 ──


class TestEvents:
    """验证 on() / emit() 核心功能"""

    @pytest.mark.asyncio
    async def test_emit_calls_all_handlers(self):
        """emit: 所有 handler 被调用"""

        called = []

        @on("test:all")
        async def handler_a(**_kw):
            called.append("a")

        @on("test:all")
        async def handler_b(**_kw):
            called.append("b")

        await emit("test:all", x=1)
        assert called == ["a", "b"]

    @pytest.mark.asyncio
    async def test_emit_no_handlers(self):
        """emit: 没有 handler 不报错"""
        await emit("test:no_handler", x=1)

    @pytest.mark.asyncio
    async def test_handler_exception_logged_not_raised(self):
        """emit: handler 抛异常只记日志不传播"""

        @on("test:bad")
        async def bad_handler(**_kw):
            raise ValueError("handler error")

        @on("test:bad")
        async def good_handler(**_kw):
            called.append("good")

        called = []
        await emit("test:bad")
        assert called == ["good"]

    @pytest.mark.asyncio
    async def test_sync_handler_works(self):
        """emit: 同步 handler 也能正确执行"""

        @on("test:sync")
        def sync_handler(**_kw):
            called.append("sync")

        called = []
        await emit("test:sync")
        assert called == ["sync"]

    @pytest.mark.asyncio
    async def test_handler_return_value_ignored(self):
        """emit: handler 返回值被忽略，不影响后续 handler"""

        values = []

        @on("test:return_val")
        async def handler_a(**_kw):
            return "blocked"

        @on("test:return_val")
        async def handler_b(**_kw):
            values.append("b")

        await emit("test:return_val")
        assert values == ["b"]


# ── 内置钩子验证 ──


class TestBuiltinHooks:
    """验证内置钩子通过 harness/hooks/__init__ 正确加载"""

    def test_all_events_registered(self):
        """所有预期事件都有 handler"""
        assert "tool:start" in events._handlers
        assert "agent:start" in events._handlers
        assert "agent:end" in events._handlers
        assert "turn:start" in events._handlers
        assert "turn:end" in events._handlers
        assert "llm:start" in events._handlers

    def test_log_hook_has_all_handlers(self):
        """log.py 注册了 6 个 handler"""
        # agent:start × 1 + turn:start × 1 + llm:start × 1
        # + turn:end × 1 + agent:end × 1 + tool:start × 1
        total = (
            len(events._handlers.get("agent:start", []))
            + len(events._handlers.get("turn:start", []))
            + len(events._handlers.get("llm:start", []))
            + len(events._handlers.get("turn:end", []))
            + len(events._handlers.get("agent:end", []))
            + len(events._handlers.get("tool:start", []))
        )
        assert total == 6

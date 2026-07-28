"""
事件系统和钩子集成测试
"""

import asyncio

import pytest

from app.harness.hooks import events
from app.harness.hooks.events import on, emit, reset


async def _drain():
    """反复让出控制权，等待 consumer 消费完所有事件"""
    for _ in range(20):
        await asyncio.sleep(0)


class TestEvents:
    """验证 on() / emit() 核心功能"""

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        reset()
        yield
        reset()

    @pytest.mark.asyncio
    async def test_emit_calls_all_handlers(self):
        called = []

        @on("test:all")
        async def handler_a(data: dict):
            called.append("a")

        @on("test:all")
        async def handler_b(data: dict):
            called.append("b")

        emit("test:all", {"x": 1})
        await _drain()
        assert called == ["a", "b"]

    @pytest.mark.asyncio
    async def test_emit_no_handlers(self):
        emit("test:no_handler", {"x": 1})
        await _drain()

    @pytest.mark.asyncio
    async def test_handler_exception_logged_not_raised(self):
        @on("test:bad")
        async def bad_handler(data: dict):
            raise ValueError("handler error")

        @on("test:bad")
        async def good_handler(data: dict):
            called.append("good")

        called = []
        emit("test:bad")
        await _drain()
        assert called == ["good"]

    @pytest.mark.asyncio
    async def test_sync_handler_works(self):
        @on("test:sync")
        def sync_handler(data: dict):
            called.append("sync")

        called = []
        emit("test:sync")
        await _drain()
        assert called == ["sync"]

    @pytest.mark.asyncio
    async def test_handler_return_value_ignored(self):
        values = []

        @on("test:return_val")
        async def handler_a(data: dict):
            return "ignored"

        @on("test:return_val")
        async def handler_b(data: dict):
            values.append("b")

        emit("test:return_val")
        await _drain()
        assert values == ["b"]


class TestBuiltinHooks:
    """验证内置钩子加载"""

    def test_all_events_registered(self):
        assert "agent:start" in events._handlers
        assert "agent:end" in events._handlers
        assert "turn:start" in events._handlers
        assert "turn:end" in events._handlers
        assert "llm:start" in events._handlers
        assert "llm:end" in events._handlers
        assert "tool:end" in events._handlers

    def test_handler_counts(self):
        total = (
            len(events._handlers.get("agent:start", []))   # log + jsonl = 2
            + len(events._handlers.get("turn:start", []))   # jsonl = 1
            + len(events._handlers.get("llm:start", []))    # jsonl = 1
            + len(events._handlers.get("llm:end", []))      # log + jsonl = 2
            + len(events._handlers.get("agent:end", []))    # jsonl = 1
            + len(events._handlers.get("tool:end", []))     # jsonl = 1
        )
        assert total == 8

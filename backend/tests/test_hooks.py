"""
事件系统和钩子集成测试

测试：
1. events.py 的 on() / emit() / emit_collect() 基础功能
2. sanitize.py 的 base64 清洗和截断
3. 通过 ToolRegistry 验证 tool:pre / tool:post 事件流程
"""

import json

import pytest

from app.harness.hooks import events
from app.harness.hooks.events import emit, emit_collect, on
from app.harness.hooks.sanitize import sanitize_tool_result
from app.harness.tool_registry import ToolDef, ToolRegistry


# ── 基础事件测试 ──


class TestEvents:
    """验证 on() / emit() / emit_collect() 核心功能"""

    @pytest.mark.asyncio
    async def test_emit_fire_and_forget(self):
        """emit: 所有 handler 被调用，返回值被忽略"""

        called = []

        @on("test:forget")
        async def handler_a(**_kw):
            called.append("a")
            return "block_value"  # 应被忽略

        @on("test:forget")
        async def handler_b(**_kw):
            called.append("b")

        await emit("test:forget", x=1)
        assert called == ["a", "b"]

    @pytest.mark.asyncio
    async def test_emit_collect_collects_non_none(self):
        """emit_collect: 收集非 None 返回值"""

        @on("test:collect")
        async def handler_a(**_kw):
            return "result_a"

        @on("test:collect")
        async def handler_b(**_kw):
            return None

        @on("test:collect")
        async def handler_c(**_kw):
            return "result_c"

        results = await emit_collect("test:collect", x=1)
        assert results == ["result_a", "result_c"]

    @pytest.mark.asyncio
    async def test_emit_collect_empty_when_all_none(self):
        """emit_collect: 所有 handler 返回 None 时得到空列表"""

        @on("test:empty_collect")
        async def handler_a(**_kw):
            return None

        @on("test:empty_collect")
        async def handler_b(**_kw):
            return None

        results = await emit_collect("test:empty_collect")
        assert results == []

    @pytest.mark.asyncio
    async def test_emit_no_handlers(self):
        """emit: 没有 handler 不报错"""
        await emit("test:no_handler", x=1)

    @pytest.mark.asyncio
    async def test_emit_collect_no_handlers(self):
        """emit_collect: 没有 handler 返回空列表"""
        results = await emit_collect("test:no_handler_collect")
        assert results == []

    @pytest.mark.asyncio
    async def test_handler_exception_logged_not_raised(self):
        """emit: handler 抛异常只记日志不传播"""

        @on("test:bad_handler")
        async def bad_handler(**_kw):
            raise ValueError("handler error")

        @on("test:bad_handler")
        async def good_handler(**_kw):
            called.append("good")

        called = []
        await emit("test:bad_handler")  # 不应抛异常
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
    async def test_sync_handler_collect(self):
        """emit_collect: 同步 handler 返回值被收集"""

        @on("test:sync_collect")
        def sync_handler(**_kw):
            return "sync_result"

        results = await emit_collect("test:sync_collect")
        assert results == ["sync_result"]


# ── sanitize 钩子测试 ──


class TestSanitizeHook:
    """验证 sanitize.py 的清洗逻辑"""

    async def _call_sanitize(self, result: str) -> str:
        """模拟 emit_collect("tool:post", ...) 的返回"""
        cleaned = await sanitize_tool_result(tool_name="test", result=result)
        return cleaned

    @pytest.mark.asyncio
    async def test_base64_stripped(self):
        """base64 头像数据被替换为空字符串"""
        original = '{"avatar": "data:image/png;base64,' + 'A' * 200 + '", "name": "test"}'
        result = await self._call_sanitize(original)
        assert '"avatar": ""' in result
        assert '"name": "test"' in result

    @pytest.mark.asyncio
    async def test_short_result_not_truncated(self):
        """短于限制的结果不被截断，返回 None"""
        text = "x" * 100
        result = await self._call_sanitize(text)
        assert result is None  # 无变化时不返回内容

    @pytest.mark.asyncio
    async def test_long_result_truncated(self):
        """超过 50000 字符的结果被截断"""
        text = "x" * 60000
        result = await self._call_sanitize(text)
        assert result is not None
        assert len(result) < 50050  # 截断后加后缀
        assert result.endswith("…(结果过长已截断)")

    @pytest.mark.asyncio
    async def test_no_base64_no_change(self):
        """无 base64 数据的字符串无变化，返回 None"""
        text = '{"a": 1, "b": "hello"}'
        result = await self._call_sanitize(text)
        assert result is None


# ── 通过 ToolRegistry 验证事件集成 ──


class TestToolRegistryHooks:
    """验证 ToolRegistry.execute() 正确触发 tool:pre / tool:post"""

    @pytest.mark.asyncio
    async def test_tool_pre_blocks_execution(self):
        """tool:pre 返回非 None 时阻止工具执行"""
        executed = [False]

        @on("tool:pre")
        async def block_hook(tool_name, **_kw):
            if tool_name == "blocked_tool":
                return "blocked by hook"

        r = ToolRegistry()

        async def _my_tool():
            executed[0] = True
            return "done"

        td = ToolDef("blocked_tool", "test", _my_tool)
        r.register(td)

        result = await r.execute("db", 1, {
            "function": {"name": "blocked_tool", "arguments": "{}"}
        })

        assert "blocked by hook" in str(result)
        assert executed[0] is False  # 工具未被执行

    @pytest.mark.asyncio
    async def test_tool_post_modifies_result(self):
        """tool:post 返回字符串时替换工具结果"""
        original_result = "original"
        modified_result = "modified"

        @on("tool:post")
        async def modify_hook(tool_name, result, **_kw):
            if result == "original":
                return modified_result

        r = ToolRegistry()

        async def _my_tool():
            return "original"

        td = ToolDef("modify_tool", "test", _my_tool)
        r.register(td)

        result = await r.execute("db", 1, {
            "function": {"name": "modify_tool", "arguments": "{}"}
        })

        assert "modified" in str(result)

    @pytest.mark.asyncio
    async def test_builtin_hooks_active(self):
        """内置钩子被正确加载（通过 harness/hooks/__init__）"""
        from app.harness.hooks import events as evt
        # 内置钩子应该注册了 tool:post 和 tool:pre
        assert "tool:pre" in evt._handlers
        assert "tool:post" in evt._handlers
        assert "agent:start" in evt._handlers
        assert "agent:turn" in evt._handlers
        assert "agent:end" in evt._handlers

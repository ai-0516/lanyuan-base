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
        assert "llm:error" in events._handlers
        assert "tool:start" in events._handlers
        assert "tool:end" in events._handlers
        assert "session:end" in events._handlers

    def test_handler_counts(self):
        total = (
            len(events._handlers.get("agent:start", []))  # log+jsonl+stats=3
            + len(events._handlers.get("turn:start", []))   # log + jsonl = 2
            + len(events._handlers.get("turn:end", []))     # log + jsonl = 2
            + len(events._handlers.get("llm:start", []))    # log + jsonl = 2
            + len(events._handlers.get("llm:end", []))      # log + jsonl + stats = 3
            + len(events._handlers.get("llm:error", []))    # log + jsonl = 2
            + len(events._handlers.get("tool:start", []))   # log + jsonl = 2
            + len(events._handlers.get("tool:end", []))     # log + jsonl + large_tool = 3
            + len(events._handlers.get("agent:end", []))    # log + jsonl + stats = 3
            + len(events._handlers.get("session:end", []))  # memory_extract = 1（2026-08-03 粒度设计）
        )
        assert total == 23


# ═══════════════════════════════════════════
#  SESSION_END → memory_extract 正向链路（端到端）
# ═══════════════════════════════════════════

class TestSessionEndExtractE2E:
    """emit SESSION_END → hook 消费 → DB 读消息 → LLM 抽取 → 记忆落库"""

    async def _setup_user_and_session(self) -> tuple[int, int]:
        """建用户 + 会话 + 写入 2 条消息（user + assistant）"""
        from app.core.database import async_session_factory
        from app.models.conversation import Conversation, Message
        from app.models.user import User

        async with async_session_factory() as db:
            user = User(openid="e2e-openid", nickname="e2e用户", avatar="")
            db.add(user)
            await db.flush()
            conv = Conversation(user_id=user.id, title="e2e测试")
            db.add(conv)
            await db.flush()
            db.add_all([
                Message(conversation_id=conv.id, role="user",
                        content="我叫张三，喜欢简洁的回复"),
                Message(conversation_id=conv.id, role="assistant",
                        content="好的，已了解。"),
            ])
            await db.commit()
            return user.id, conv.id

    async def test_session_end_extract_writes_memory(self, monkeypatch):
        """正向链路：SESSION_END → extract → 记忆写入 user_memories"""
        import json as _json

        from app.core.database import async_session_factory
        from app.harness.hooks.memory_extract import on_session_end
        from app.harness.memory import memory as memory_harness
        from app.models.user_memory import UserMemory

        uid, conv_id = await self._setup_user_and_session()

        # mock LLM：返回一条记忆
        async def fake_call_llm(prompt):
            return _json.dumps([{
                "name": "user-name",
                "type": "user",
                "description": "用户名字",
                "body": "我叫张三",
            }])

        monkeypatch.setattr(memory_harness, "_call_llm", fake_call_llm)

        # 直接调用 hook（等价于 emit(SESSION_END, data) 的消费路径）
        await on_session_end({"user_id": uid, "session_id": conv_id})

        # 断言记忆已写入
        async with async_session_factory() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(UserMemory).where(UserMemory.user_id == uid)
            )
            mems = list(result.scalars().all())

        assert len(mems) == 1
        assert mems[0].name == "user-name"
        assert mems[0].type == "user"
        assert "张三" in mems[0].body

    async def test_session_end_no_messages_skips(self, monkeypatch):
        """会话无消息 → hook 安全返回，不调 LLM 不写记忆"""
        from app.core.database import async_session_factory
        from app.harness.hooks.memory_extract import on_session_end
        from app.harness.memory import memory as memory_harness
        from app.models.conversation import Conversation
        from app.models.user import User

        async with async_session_factory() as db:
            user = User(openid="e2e-empty", nickname="e2e空", avatar="")
            db.add(user)
            await db.flush()
            conv = Conversation(user_id=user.id, title="空会话")
            db.add(conv)
            await db.commit()
            uid, conv_id = user.id, conv.id

        called = False

        async def fake_call_llm(prompt):
            nonlocal called
            called = True
            return "[]"

        monkeypatch.setattr(memory_harness, "_call_llm", fake_call_llm)
        await on_session_end({"user_id": uid, "session_id": conv_id})

        assert called is False  # 无消息不触发 LLM

    async def test_session_end_missing_ids_skips(self, monkeypatch):
        """缺 user_id/session_id → 直接返回，不报错"""
        from app.harness.hooks.memory_extract import on_session_end
        from app.harness.memory import memory as memory_harness

        called = False

        async def fake_call_llm(prompt):
            nonlocal called
            called = True
            return "[]"

        monkeypatch.setattr(memory_harness, "_call_llm", fake_call_llm)
        await on_session_end({})  # 空 data
        await on_session_end({"user_id": 1})  # 缺 session_id
        assert called is False

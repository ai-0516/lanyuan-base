"""跨会话记忆（#9）单元测试 — provider 存储/检索/API

覆盖：
- MySQLMemoryProvider: add / list_all / search / delete / 类型校验
- 用户隔离（A 的记忆 B 看不到）
- 上限触发合并（mock LLM）
- 关键词召回打分
- REST API + @tool
"""

import pytest
from sqlalchemy import select

from app.core.database import async_session_factory, init_db
from app.harness.memory import (
    MemoryLimitError,
    MySQLMemoryProvider,
    get_provider,
)
from app.harness import context
from app.models.user import User
from app.models.user_memory import UserMemory


async def _clear_db():
    from sqlalchemy import text
    try:
        async with async_session_factory() as session:
            for t in ["user_memories", "messages", "conversations", "users"]:
                await session.execute(text(f"DELETE FROM {t}"))
            await session.commit()
    except Exception:
        pass


@pytest.fixture(autouse=True)
async def setup_db():
    """每个测试前后清理数据库"""
    await _clear_db()
    await init_db()
    yield
    await _clear_db()


async def _create_user(nickname: str = "测试用户", openid: str = "test") -> int:
    async with async_session_factory() as db:
        user = User(openid=openid, nickname=nickname, avatar="")
        db.add(user)
        await db.commit()
        return user.id


# ═══════════════════════════════════════════
#  provider 存储
# ═══════════════════════════════════════════

class TestMemoryAdd:
    async def test_add_basic(self):
        """基本写入"""
        uid = await _create_user()
        provider = get_provider()
        async with async_session_factory() as db:
            mem = await provider.add(
                db, uid, name="user-name", type="user",
                description="用户名字", body="用户叫张三",
            )
            await db.commit()

        assert mem.id > 0
        assert mem.name == "user-name"
        assert mem.type == "user"

    async def test_add_invalid_type_fallback(self):
        """非法 type 回退为 user"""
        uid = await _create_user()
        provider = get_provider()
        async with async_session_factory() as db:
            mem = await provider.add(
                db, uid, name="x", type="invalid-type",
                description="d", body="b",
            )
            await db.commit()
        assert mem.type == "user"

    async def test_add_exceeds_limit_triggers_consolidate(self, monkeypatch):
        """超限触发合并（mock LLM 返回合并结果）"""
        uid = await _create_user()
        provider = MySQLMemoryProvider()

        # 写入接近上限（临时降低上限）
        monkeypatch.setattr("app.harness.memory.MAX_PER_USER", 3)

        async def _fake_llm(prompt: str) -> str:
            return (
                '[{"name": "merged", "type": "user", '
                '"description": "合并后", "body": "合并内容"}]'
            )
        monkeypatch.setattr(provider, "_call_llm", _fake_llm)

        async with async_session_factory() as db:
            for i in range(3):
                await provider.add(
                    db, uid, name=f"m{i}", type="user",
                    description=f"d{i}", body=f"b{i}",
                )
            await db.flush()

            # 第 4 条触发合并（3 条 → 1 条）后再写入
            mem = await provider.add(
                db, uid, name="new", type="user",
                description="新", body="新内容",
            )
            await db.commit()

        async with async_session_factory() as db:
            all_mem = await provider.list_all(db, uid)
        assert len(all_mem) == 2  # 合并后的 1 条 + 新写入 1 条
        names = {m.name for m in all_mem}
        assert "new" in names
        assert "merged" in names

    async def test_add_after_consolidate_still_full_raises(self, monkeypatch):
        """合并后仍满 → 抛 MemoryLimitError"""
        uid = await _create_user()
        provider = MySQLMemoryProvider()
        monkeypatch.setattr("app.harness.memory.MAX_PER_USER", 2)

        async def _fake_llm(prompt: str) -> str:
            return "[]"  # 合并为空，没腾出空间
        monkeypatch.setattr(provider, "_call_llm", _fake_llm)

        async with async_session_factory() as db:
            for i in range(2):
                await provider.add(
                    db, uid, name=f"m{i}", type="user",
                    description=f"d{i}", body=f"b{i}",
                )
            await db.flush()
            with pytest.raises(MemoryLimitError):
                await provider.add(
                    db, uid, name="new", type="user",
                    description="新", body="新内容",
                )
            await db.rollback()


class TestMemoryListSearch:
    async def test_list_all_ordered(self):
        """列表按更新时间倒序"""
        uid = await _create_user()
        provider = get_provider()
        async with async_session_factory() as db:
            await provider.add(db, uid, name="first", type="user",
                               description="第一条", body="内容A")
            await provider.add(db, uid, name="second", type="user",
                               description="第二条", body="内容B")
            await db.commit()

        async with async_session_factory() as db:
            memories = await provider.list_all(db, uid)
        assert len(memories) == 2
        # 后写入的 second 在前（同秒时按 id 倒序）
        assert memories[0].name == "second"
        assert memories[1].name == "first"

    async def test_user_isolation(self):
        """用户 A 的记忆用户 B 不可见"""
        uid_a = await _create_user("A", "a")
        uid_b = await _create_user("B", "b")
        provider = get_provider()
        async with async_session_factory() as db:
            await provider.add(db, uid_a, name="a-mem", type="user",
                               description="A的记忆", body="AAA")
            await provider.add(db, uid_b, name="b-mem", type="user",
                               description="B的记忆", body="BBB")
            await db.commit()

        async with async_session_factory() as db:
            mems_a = await provider.list_all(db, uid_a)
            mems_b = await provider.list_all(db, uid_b)
        assert len(mems_a) == 1 and mems_a[0].name == "a-mem"
        assert len(mems_b) == 1 and mems_b[0].name == "b-mem"

    async def test_delete_only_own(self):
        """只能删除自己的记忆"""
        uid_a = await _create_user("A", "a")
        uid_b = await _create_user("B", "b")
        provider = get_provider()
        async with async_session_factory() as db:
            mem = await provider.add(db, uid_a, name="a-mem", type="user",
                                     description="A", body="AAA")
            await db.commit()
            mem_id = mem.id

            # B 删 A 的记忆 → 失败
            deleted = await provider.delete(db, uid_b, mem_id)
            assert deleted is False
            # A 自己删 → 成功
            deleted = await provider.delete(db, uid_a, mem_id)
            assert deleted is True
            await db.commit()

        async with async_session_factory() as db:
            rest = await provider.list_all(db, uid_a)
        assert rest == []

    async def test_search_keyword_scoring(self):
        """关键词召回：body 命中优先"""
        uid = await _create_user()
        provider = get_provider()
        async with async_session_factory() as db:
            await provider.add(db, uid, name="heat", type="reference",
                               description="暖气费", body="3号楼暖气费问题处理中")
            await provider.add(db, uid, name="name", type="user",
                               description="名字", body="我叫张三")
            await db.commit()

        async with async_session_factory() as db:
            hits = await provider.search(db, uid, ["暖气"], limit=5)
        assert len(hits) == 1
        assert hits[0].name == "heat"

        # 无关键词 → 空
        async with async_session_factory() as db:
            hits = await provider.search(db, uid, [], limit=5)
        assert hits == []


# ═══════════════════════════════════════════
#  context 注入
# ═══════════════════════════════════════════

class TestContextInjection:
    async def test_build_memory_index(self):
        """索引文本生成"""
        uid = await _create_user()
        provider = get_provider()
        async with async_session_factory() as db:
            await provider.add(db, uid, name="user-name", type="user",
                               description="用户名字", body="张三")
            await db.commit()
            memories = await provider.list_all(db, uid)

        index = context.build_memory_index(memories)
        assert "user-name" in index
        assert "用户名字" in index
        assert memories[0].type in index

    async def test_build_memory_index_empty(self):
        assert context.build_memory_index([]) == ""

    async def test_build_relevant_section(self):
        """相关记忆完整内容"""
        uid = await _create_user()
        provider = get_provider()
        async with async_session_factory() as db:
            mem = await provider.add(db, uid, name="user-name", type="user",
                                     description="用户名字", body="我叫张三，住3号楼")
            await db.commit()

        section = context.build_relevant_section([mem])
        assert "我叫张三，住3号楼" in section

    async def test_build_deepseek_messages_with_memory(self):
        """build_deepseek_messages 注入记忆索引和相关记忆"""
        uid = await _create_user()
        provider = get_provider()
        async with async_session_factory() as db:
            mem = await provider.add(db, uid, name="user-name", type="user",
                                     description="用户名字", body="我叫张三")
            await db.commit()
            memories = await provider.list_all(db, uid)

        index = context.build_memory_index(memories)
        messages = context.build_deepseek_messages(
            [], "你好", memory_index=index, relevant_memories=[mem],
        )
        assert messages[0]["role"] == "system"
        assert "我叫张三" in messages[0]["content"]
        assert "user-name" in messages[0]["content"]

    async def test_build_deepseek_messages_no_memory(self):
        """无记忆时 system 不含实际记忆内容"""
        messages = context.build_deepseek_messages([], "你好")
        assert messages[0]["role"] == "system"
        # SYSTEM_PROMPT 含记忆说明文字，但不含实际索引/相关记忆段
        assert "你的记忆索引：" not in messages[0]["content"]
        assert "与当前对话相关的记忆：" not in messages[0]["content"]

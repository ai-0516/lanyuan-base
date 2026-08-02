"""跨会话记忆（#9）单元测试 — provider 存储/检索/API

覆盖：
- MySQLMemoryProvider: add / list_all / search / delete / 类型校验
- 用户隔离（A 的记忆 B 看不到）
- 上限触发合并（mock LLM）
- 关键词召回打分
- REST API + @tool
"""

import pytest

from app.core.database import async_session_factory, init_db
from app.harness.memory import (
    MemoryLimitError,
    MySQLMemoryProvider,
    get_provider,
)
from app.harness import context
from app.models.user import User


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


# ═══════════════════════════════════════════
#  memory_select._llm_select（LLM 选相关记忆）
# ═══════════════════════════════════════════

class _FakeMemory:
    """最小记忆对象：仅承载 name/type/description（_llm_select 只用这三个字段拼 catalog）"""

    def __init__(self, name: str, type: str = "user", description: str = ""):
        self.name = name
        self.type = type
        self.description = description


class TestLlmSelect:
    """_llm_select：LLM 返回索引 → 映射；越界/非法 → 过滤；异常 → None 触发降级"""

    async def _run_select(self, monkeypatch, llm_tokens: list, memories: list):
        """mock streaming.deepseek_chat 为 token 流，执行 _llm_select"""
        from app.harness import memory_select

        async def fake_deepseek_chat(messages):
            for tok in llm_tokens:
                yield ("token", tok)
            yield ("done", "")

        monkeypatch.setattr(
            "app.harness.streaming.deepseek_chat", fake_deepseek_chat
        )
        return await memory_select._llm_select("用户消息", memories)

    async def test_llm_select_success_maps_indices(self, monkeypatch):
        """LLM 返回 [0, 3] → 正确映射到对应记忆"""
        memories = [
            _FakeMemory(f"mem-{i}", description=f"desc-{i}") for i in range(5)
        ]
        selected = await self._run_select(monkeypatch, ["[0, 3]"], memories)
        assert [m.name for m in selected] == ["mem-0", "mem-3"]

    async def test_llm_select_out_of_range_filtered(self, monkeypatch):
        """LLM 返回越界/负数索引 → 过滤，只保留合法索引"""
        memories = [
            _FakeMemory(f"mem-{i}", description=f"desc-{i}") for i in range(3)
        ]
        # 99 越界、-1 负数、2 合法
        selected = await self._run_select(monkeypatch, ["[99, -1, 2]"], memories)
        assert [m.name for m in selected] == ["mem-2"]

    async def test_llm_select_empty_array(self, monkeypatch):
        """LLM 返回空数组 → 空列表（不选任何记忆）"""
        memories = [
            _FakeMemory("mem-0", description="desc-0"),
            _FakeMemory("mem-1", description="desc-1"),
        ]
        selected = await self._run_select(monkeypatch, ["[]"], memories)
        assert selected == []

    async def test_llm_select_markdown_code_block(self, monkeypatch):
        """LLM 输出带 markdown 代码块 → 正则提取 JSON 数组"""
        memories = [
            _FakeMemory(f"mem-{i}", description=f"desc-{i}") for i in range(4)
        ]
        selected = await self._run_select(
            monkeypatch, ["```json\n[1, 2]\n```"], memories
        )
        assert [m.name for m in selected] == ["mem-1", "mem-2"]

    async def test_llm_select_error_returns_none(self, monkeypatch):
        """LLM 调用抛错 → 返回 None（触发关键词降级）"""
        from app.harness import memory_select

        async def fake_deepseek_chat_error(messages):
            yield ("error", "boom")

        monkeypatch.setattr(
            "app.harness.streaming.deepseek_chat", fake_deepseek_chat_error
        )
        memories = [_FakeMemory("mem-0", description="desc-0")]
        result = await memory_select._llm_select("用户消息", memories)
        assert result is None


# ═══════════════════════════════════════════
#  memory_select.select_relevant（方案 b：关键词优先 + LLM 兜底）
# ═══════════════════════════════════════════

class TestSelectRelevant:
    """select_relevant：关键词命中→零 LLM 调用；无命中→LLM 兜底；失败→空"""

    async def _add_memory(self, provider, uid, name, body, description=""):
        async with async_session_factory() as db:
            mem = await provider.add(
                db, uid, name=name, type="user",
                description=description or name, body=body,
            )
            await db.commit()
            return mem

    async def test_keyword_hit_no_llm(self, monkeypatch):
        """关键词命中 → 直接返回，LLM 完全不参与（方案 b 核心）"""
        from app.harness import memory_select

        uid = await _create_user()
        provider = get_provider()
        await self._add_memory(provider, uid, "food", "用户爱吃麻辣火锅")

        calls = {"n": 0}

        async def fake_llm(msg, mems):
            calls["n"] += 1
            return []

        monkeypatch.setattr("app.harness.memory_select._llm_select", fake_llm)

        async with async_session_factory() as db:
            result = await memory_select.select_relevant(db, uid, "我想吃火锅", provider)

        assert calls["n"] == 0
        assert len(result) == 1
        assert "火锅" in result[0].body

    async def test_chinese_bigram_hit(self, monkeypatch):
        """中文 2 字滑窗命中（「爬山」），零 LLM 调用"""
        from app.harness import memory_select

        uid = await _create_user()
        provider = get_provider()
        await self._add_memory(provider, uid, "hobby", "爱好：周末爬山")

        calls = {"n": 0}

        async def fake_llm(msg, mems):
            calls["n"] += 1
            return []

        monkeypatch.setattr("app.harness.memory_select._llm_select", fake_llm)

        async with async_session_factory() as db:
            result = await memory_select.select_relevant(db, uid, "周末去爬山怎么样", provider)

        assert calls["n"] == 0
        assert any("爬山" in (m.body or "") for m in result)

    async def test_keyword_miss_llm_fallback(self, monkeypatch):
        """关键词无命中 → LLM 兜底召回（语义相关但无共同词）"""
        from app.harness import memory_select

        uid = await _create_user()
        provider = get_provider()
        await self._add_memory(provider, uid, "hobby", "爱好：周末爬山")

        async def fake_llm(msg, mems):
            return [mems[0]]

        monkeypatch.setattr("app.harness.memory_select._llm_select", fake_llm)

        async with async_session_factory() as db:
            result = await memory_select.select_relevant(db, uid, "今天天气怎么样", provider)

        assert len(result) == 1
        assert result[0].name == "hobby"

    async def test_keyword_miss_llm_fail_returns_empty(self, monkeypatch):
        """关键词无命中 + LLM 失败（None）→ 空列表，不影响主流程"""
        from app.harness import memory_select

        uid = await _create_user()
        provider = get_provider()
        await self._add_memory(provider, uid, "hobby", "爱好：周末爬山")

        async def fake_llm(msg, mems):
            return None

        monkeypatch.setattr("app.harness.memory_select._llm_select", fake_llm)

        async with async_session_factory() as db:
            result = await memory_select.select_relevant(db, uid, "今天天气怎么样", provider)

        assert result == []

    async def test_empty_message_returns_empty(self):
        """空白消息 → 空列表（不查库不调 LLM）"""
        from app.harness import memory_select

        uid = await _create_user()
        provider = get_provider()
        async with async_session_factory() as db:
            result = await memory_select.select_relevant(db, uid, "   ", provider)
        assert result == []

    async def test_no_memories_no_llm(self, monkeypatch):
        """无记忆 → 空列表，LLM 不参与"""
        from app.harness import memory_select

        uid = await _create_user()
        provider = get_provider()

        calls = {"n": 0}

        async def fake_llm(msg, mems):
            calls["n"] += 1
            return []

        monkeypatch.setattr("app.harness.memory_select._llm_select", fake_llm)

        async with async_session_factory() as db:
            result = await memory_select.select_relevant(db, uid, "hello world", provider)

        assert result == []
        assert calls["n"] == 0


class TestExtractKeywords:
    """_extract_keywords：英文单词 + 中文 2 字滑窗"""

    def test_english_words(self):
        from app.harness.memory_select import _extract_keywords

        assert _extract_keywords("I love hiking") == ["love", "hiking"]

    def test_chinese_bigrams(self):
        from app.harness.memory_select import _extract_keywords

        kws = _extract_keywords("我想吃火锅")
        assert "火锅" in kws

    def test_dedup_and_cap(self):
        from app.harness.memory_select import _extract_keywords

        kws = _extract_keywords("吃火锅 吃火锅 吃火锅")
        assert len(kws) <= 20
        assert kws == list(dict.fromkeys(kws))

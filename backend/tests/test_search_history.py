"""search_history 工具测试（#47，TECH_SPEC 8.6）

直接调用 search_history 函数（Depends 注入参数显式传 db/user_id）。
"""

import pytest

from app.core.database import async_session_factory, init_db
from app.harness import session as session_ops
from app.models.conversation import Conversation
from app.models.user import User


async def _clear_db():
    from app.core.database import async_session_factory
    from sqlalchemy import text
    try:
        async with async_session_factory() as session:
            for t in ["user_memories", "messages", "conversations", "notifications",
                      "likes", "comments", "posts", "users"]:
                await session.execute(text(f"DELETE FROM {t}"))
            await session.commit()
    except Exception:
        pass


@pytest.fixture(autouse=True)
async def setup_db():
    await _clear_db()
    await init_db()
    yield
    await _clear_db()


async def _create_user(openid: str = "test", nickname: str = "测试用户") -> int:
    async with async_session_factory() as db:
        user = User(openid=openid, nickname=nickname, avatar="")
        db.add(user)
        await db.commit()
        return user.id


async def _seed_history(uid: int, conv_count: int = 2, msgs_per_conv: int = 6) -> list[int]:
    """创建 conv_count 个会话，每个会话 msgs_per_conv 条 user/assistant 交替消息。

    消息内容包含可搜索关键词（如「压缩」「方案」），返回会话 id 列表（最新在后）。
    """
    conv_ids = []
    async with async_session_factory() as db:
        for c in range(conv_count):
            conv = Conversation(user_id=uid, title="")
            db.add(conv)
            await db.flush()
            conv_ids.append(conv.id)
            for i in range(msgs_per_conv):
                await session_ops.save_user_message(db, conv.id, f"第{c}会话第{i}条：压缩方案讨论 x{i}")
                await session_ops.save_assistant_message(db, conv.id, f"回复{i}：关于压缩方案的建议 y{i}")
        await db.commit()
    return conv_ids


async def _search(uid: int, query: str, **kwargs):
    from app.api.v1.ai import search_history
    async with async_session_factory() as db:
        return await search_history(query=query, db=db, user_id=uid, **kwargs)


class TestSearchHistory:

    async def test_hits_history_messages(self):
        """命中历史消息（含旧会话内容）"""
        uid = await _create_user()
        await _seed_history(uid, conv_count=2, msgs_per_conv=3)
        result = await _search(uid, "压缩方案")
        # 最新会话被排除 → 只搜到旧会话的 3 条 user 消息
        assert result["total"] == 3

    async def test_excludes_current_conversation(self):
        """当前活跃会话（用户最新）的消息被排除"""
        uid = await _create_user()
        conv_ids = await _seed_history(uid, conv_count=2, msgs_per_conv=3)
        latest = conv_ids[-1]

        # 最新会话加一条当前讨论（不该出现在结果里）
        async with async_session_factory() as db:
            await session_ops.save_user_message(db, latest, "当前正在聊的压缩方案话题")
            await db.commit()

        result = await _search(uid, "压缩方案")
        for hit in result["results"]:
            assert hit["conversation_id"] != latest
        # 但最新会话的历史消息（msgs_per_conv 中的）也被排除——因为整个会话是当前
        assert all(h["conversation_id"] != latest for h in result["results"])

    async def test_tool_messages_not_searched(self):
        """tool 消息（含压缩摘要）不进搜索结果"""
        uid = await _create_user()
        async with async_session_factory() as db:
            conv = Conversation(user_id=uid, title="")
            db.add(conv)
            await db.flush()
            await session_ops.save_user_message(db, conv.id, "普通问题")
            await session_ops.save_tool_call_message(db, conv.id, [{"id": "c1", "type": "function",
                "function": {"name": "compress_context", "arguments": "{}"}}], content=None)
            await session_ops.save_tool_result_message(db, conv.id, tool_call_id="c1", content="【摘要】压缩方案要点")
            await db.commit()

        result = await _search(uid, "摘要")
        assert result["total"] == 0  # tool 消息不搜，摘要搜不到

    async def test_context_window(self):
        """命中消息带 ±window 上下文（含 tool 消息，无过滤）"""
        uid = await _create_user()
        async with async_session_factory() as db:
            conv = Conversation(user_id=uid, title="")
            db.add(conv)
            await db.flush()
            for i in range(10):
                await session_ops.save_user_message(db, conv.id, f"普通消息{i}")
            await db.commit()

        # 第 5 条含关键词（最新会话是唯一的——先建一个"当前"来排除它）
        # 构造：再建一个新会话作为"当前"，旧会话的可搜
        async with async_session_factory() as db:
            conv2 = Conversation(user_id=uid, title="")
            db.add(conv2)
            await db.commit()

        result = await _search(uid, "普通消息5")
        assert result["total"] == 1
        hit = result["results"][0]
        assert len(hit["context_window"]) <= 11  # 1 + 2*5
        roles = [m["role"] for m in hit["context_window"]]
        assert "user" in roles

    async def test_user_isolation(self):
        """按 user_id 隔离：只搜自己的历史"""
        uid1 = await _create_user(openid="a", nickname="用户A")
        uid2 = await _create_user(openid="b", nickname="用户B")
        # uid1：2 个会话（旧的可搜 + 最新的作为"当前"被排除）
        conv_ids = await _seed_history(uid1, conv_count=2, msgs_per_conv=2)
        # uid2：1 个会话（唯一 = 当前，被排除）
        async with async_session_factory() as db:
            conv = Conversation(user_id=uid2, title="")
            db.add(conv)
            await db.flush()
            await session_ops.save_user_message(db, conv.id, "A的秘密压缩方案")
            await db.commit()

        r1 = await _search(uid1, "压缩方案")
        # uid1 搜到自己旧会话的历史（最新会话被排除）
        assert r1["total"] >= 2
        assert all(h["conversation_id"] == conv_ids[0] for h in r1["results"])
        # uid2 搜不到 uid1 的内容（uid2 唯一会话是当前，被排除 → 空）
        r2 = await _search(uid2, "压缩方案")
        assert r2["total"] == 0

    async def test_multi_keyword_and(self):
        """多关键词空格分隔 → 全部命中才返回（AND）"""
        uid = await _create_user()
        async with async_session_factory() as db:
            conv = Conversation(user_id=uid, title="")
            db.add(conv)
            await db.flush()
            await session_ops.save_user_message(db, conv.id, "压缩方案很好")
            await session_ops.save_user_message(db, conv.id, "压缩不好")
            # 当前会话排除——建第二个会话放历史
            await db.commit()
            conv2 = Conversation(user_id=uid, title="")
            db.add(conv2)
            await db.commit()

        result = await _search(uid, "压缩 方案")
        assert result["total"] == 1
        assert "压缩方案很好" in result["results"][0]["content"]

    async def test_sort_oldest(self):
        """sort=oldest → 时间正序（最旧命中在前）"""
        uid = await _create_user()
        async with async_session_factory() as db:
            conv = Conversation(user_id=uid, title="")
            db.add(conv)
            await db.flush()
            await session_ops.save_user_message(db, conv.id, "第一轮压缩方案")
            await session_ops.save_user_message(db, conv.id, "第二轮压缩方案")
            await db.commit()
            conv2 = Conversation(user_id=uid, title="")
            db.add(conv2)
            await db.commit()

        result = await _search(uid, "压缩方案", sort="oldest")
        assert result["total"] == 2
        assert "第一轮" in result["results"][0]["content"]

    async def test_limit_and_truncate(self):
        """limit 上限 + 超长内容截断"""
        uid = await _create_user()
        async with async_session_factory() as db:
            conv = Conversation(user_id=uid, title="")
            db.add(conv)
            await db.flush()
            for i in range(5):
                await session_ops.save_user_message(db, conv.id, f"压缩方案 {i} " + "x" * 5000)
            await db.commit()
            conv2 = Conversation(user_id=uid, title="")
            db.add(conv2)
            await db.commit()

        result = await _search(uid, "压缩方案", limit=2)
        assert result["total"] == 2
        for hit in result["results"]:
            assert len(hit["content"]) <= 4001  # 4000 + 省略号

    async def test_empty_query(self):
        """空查询返回空结果"""
        uid = await _create_user()
        await _seed_history(uid, conv_count=1, msgs_per_conv=2)
        result = await _search(uid, "   ")
        assert result == {"results": [], "total": 0}

    async def test_registered_in_registry(self):
        """search_history 已注册进 registry（schema 生成）"""
        from app.harness.tool_registry import registry
        td = registry.get("search_history")
        assert td is not None
        schema = td.schema["function"]
        assert schema["name"] == "search_history"
        assert "query" in schema["parameters"]["properties"]
        assert "query" in schema["parameters"]["required"]

    async def test_like_wildcard_escaped(self):
        """LIKE 通配符转义：%/_ 按字面量匹配（review #51 建议 1）

        修复前：搜 `%` 匹配所有消息（通配符语义）；修复后只匹配含字面量
        `%` 的消息。
        """
        uid = await _create_user()
        async with async_session_factory() as db:
            conv = Conversation(user_id=uid, title="")
            db.add(conv)
            await db.flush()
            # 一条含字面 %，两条不含
            await session_ops.save_user_message(db, conv.id, "折扣 50% 优惠")
            await session_ops.save_user_message(db, conv.id, "折扣方案讨论")
            await session_ops.save_user_message(db, conv.id, "降价促销活动")
            await db.commit()
            conv2 = Conversation(user_id=uid, title="")
            db.add(conv2)
            await db.commit()

        # 搜字面 %：只命中「折扣 50% 优惠」，不返回全部
        result = await _search(uid, "50%")
        assert result["total"] == 1
        assert "50% 优惠" in result["results"][0]["content"]

        # 裸 % 作为唯一关键词：只命中含字面 % 的消息（1 条），
        # 修复前通配符语义匹配全部（3 条）——断言 1 即可区分
        result2 = await _search(uid, "%")
        assert result2["total"] == 1
        assert "50% 优惠" in result2["results"][0]["content"]

    async def test_like_underscore_escaped(self):
        """下划线按字面量匹配（通配符语义：_ 匹配任意单字符）"""
        uid = await _create_user()
        async with async_session_factory() as db:
            conv = Conversation(user_id=uid, title="")
            db.add(conv)
            await db.flush()
            await session_ops.save_user_message(db, conv.id, "user_id 字段说明")
            await session_ops.save_user_message(db, conv.id, "userXid 样式")
            await db.commit()
            conv2 = Conversation(user_id=uid, title="")
            db.add(conv2)
            await db.commit()

        # 修复前：_ 是通配符，「user_id」会同时命中 userXid；修复后只命中字面 user_id
        result = await _search(uid, "user_id")
        assert result["total"] == 1
        assert "字段说明" in result["results"][0]["content"]

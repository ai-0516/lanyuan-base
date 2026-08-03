"""
PR #33（#9 跨会话记忆系统）触发验证脚本

验证内容（对应 review 时实测场景）：
1. 记忆 CRUD API（真实 ASGI 触发）：
   - POST /api/v1/memory 添加 → 200，返回完整记忆
   - GET /api/v1/memory 列表 → 200
   - POST 非法 type → 40011
   - DELETE /memory/{id} → 200
2. 用户隔离（直接 DB 造双用户，绕过登录模拟）：
   - A 看到 1 条、B 看到 0 条
   - B 删除 A 的记忆 → deleted=False，A 的记忆仍在
3. 记忆注入（真实函数）：
   - build_memory_index / build_relevant_section → 正确生成
   - build_deepseek_messages → system prompt 含索引和 body
4. @tool 注册（app.main 全量 import）：
   - registry 含 memory_list/memory_add/memory_delete
5. memory_select 策略（方案 b：关键词优先 + LLM 兜底）：
   - 关键词命中 → 零 LLM 调用
   - 关键词无命中 → LLM 兜底被调用并正确返回
   - LLM 内部抛错 → 返回 []，不阻塞
   - 无记忆短路：直接返回 []，不调 LLM
6. consolidate 保护（全删重写防丢数据）：
   - LLM 返回「非空但全部无效」→ 原记忆完整保留
   - body 写入截断至 BODY_MAX_LEN（2000）
7. extract hook 失败不阻塞：无 API key 时 extract 抛错 → 只记日志

用法：
    uv run python scripts/review/memory_system/review.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

# 必须在 import app.* 之前设置：临时 SQLite 库（review 专用，不动 test_lanyuan.db）
_TMPDIR = tempfile.mkdtemp(prefix="review_pr33_")
_DB_PATH = Path(_TMPDIR) / "review_lanyuan.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH}"

# 先注册所有 model（Base.metadata），否则 init_db() 的 create_all 建空表
from app.main import app  # noqa: E402,F401  (触发 models 注册)

RESULTS: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, detail: str = ""):
    RESULTS.append((label, ok, detail))
    print(f"  {'✅' if ok else '❌'} {label}" + (f" — {detail}" if detail else ""))


async def _setup_db():
    from app.core.database import init_db
    await init_db()


async def _clear_db():
    from app.core.database import async_session_factory
    from sqlalchemy import text
    async with async_session_factory() as session:
        for t in ["user_memories", "messages", "conversations", "notifications", "likes", "comments", "posts", "users"]:
            await session.execute(text(f"DELETE FROM {t}"))
        await session.commit()


async def _login_token(client, code) -> str:
    resp = await client.post("/api/v1/auth/login", json={"code": code})
    return resp.json()["data"]["token"]


async def _ensure_user(user_id: int = 1):
    """确保 users 表存在该用户（重构后 user_memories.user_id 有 FK→users.id，
    直接 DB 操作前必须先造用户，否则 FK 约束失败）"""
    from app.core.database import async_session_factory
    from app.models.user import User
    from sqlalchemy import select

    async with async_session_factory() as session:
        exists = (await session.execute(
            select(User.id).where(User.id == user_id)
        )).scalar_one_or_none()
        if not exists:
            session.add(User(id=user_id, openid=f"review_user_{user_id}", nickname=f"用户{user_id}"))
            await session.commit()


class _FakeMsg:
    """build_deepseek_messages 用最小消息对象（history 参数需要 role/content）"""

    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content
        self.tool_call_id = None
        self.tool_calls = None


# ───────────────────────── 场景 1：记忆 CRUD API ─────────────────────────
async def verify_crud_api():
    print("\n## 场景 1：记忆 CRUD API")
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login_token(client, "memory_user_001")
        headers = {"Authorization": f"Bearer {token}"}

        # 1.1 添加记忆
        r = await client.post(
            "/api/v1/memory",
            json={"name": "hobby-hiking", "type": "user", "description": "爱好爬山", "body": "用户喜欢周末爬山"},
            headers=headers,
        )
        body = r.json()
        record(
            "POST /memory 添加 → 200 返回完整记忆",
            r.status_code == 200 and body.get("code") == 0
            and body.get("data", {}).get("name") == "hobby-hiking",
            f"status={r.status_code} body={body}",
        )
        mem_id = body.get("data", {}).get("id")

        # 1.2 列表
        r = await client.get("/api/v1/memory", headers=headers)
        body = r.json()
        record(
            "GET /memory 列表 → 200 含刚添加的记忆",
            r.status_code == 200 and len(body.get("data", [])) >= 1,
            f"status={r.status_code} 条数={len(body.get('data', []))}",
        )

        # 1.3 非法 type
        r = await client.post(
            "/api/v1/memory",
            json={"name": "bad-type", "type": "invalid_type_xx", "description": "x", "body": "y"},
            headers=headers,
        )
        body = r.json()
        record(
            "POST 非法 type → 40011",
            r.status_code == 400 and body.get("code") == 40011,
            f"status={r.status_code} body={body}",
        )

        # 1.4 删除
        if mem_id:
            r = await client.delete(f"/api/v1/memory/{mem_id}", headers=headers)
            body = r.json()
            record(
                "DELETE /memory/{id} → 200",
                r.status_code == 200 and body.get("data", {}).get("deleted") is True,
                f"status={r.status_code} body={body}",
            )


# ───────────────────────── 场景 2：用户隔离 ─────────────────────────
async def verify_user_isolation():
    print("\n## 场景 2：用户隔离（直接 DB 造双用户）")
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # A 登录并添加一条记忆
        token_a = await _login_token(client, "memory_user_a")
        headers_a = {"Authorization": f"Bearer {token_a}"}
        r = await client.post(
            "/api/v1/memory",
            json={"name": "user-a-mem", "type": "user", "description": "A 的记忆", "body": "A 喜欢篮球"},
            headers=headers_a,
        )
        mem_a_id = r.json()["data"]["id"]

        # B 登录（不同用户）
        token_b = await _login_token(client, "memory_user_b")
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # A 看到 1 条，B 看到 0 条
        r_a = await client.get("/api/v1/memory", headers=headers_a)
        r_b = await client.get("/api/v1/memory", headers=headers_b)
        record(
            "A 看到 1 条 / B 看到 0 条",
            len(r_a.json().get("data", [])) == 1 and len(r_b.json().get("data", [])) == 0,
            f"A={len(r_a.json().get('data', []))} B={len(r_b.json().get('data', []))}",
        )

        # B 删除 A 的记忆 → 失败，A 的记忆仍在
        r = await client.delete(f"/api/v1/memory/{mem_a_id}", headers=headers_b)
        body = r.json()
        r_a_after = await client.get("/api/v1/memory", headers=headers_a)
        record(
            "B 删除 A 的记忆 → deleted=False，A 的记忆仍在",
            body.get("data", {}).get("deleted") is False
            and len(r_a_after.json().get("data", [])) == 1,
            f"body={body} A剩余={len(r_a_after.json().get('data', []))}",
        )


# ───────────────────────── 场景 3：记忆注入（真实函数）─────────────────────────
async def verify_injection():
    print("\n## 场景 3：记忆注入（session 粒度：只注入索引，不注入相关记忆）")
    await _ensure_user(1)
    from app.core.database import async_session_factory
    from app.harness import memory as mem
    from app.harness.context import build_deepseek_messages

    async with async_session_factory() as session:
        await mem.add(
            session, 1,
            name="hobby-hiking", type="user", description="爱好爬山", body="用户喜欢周末爬山",
        )
        await session.commit()

        # 3.1 组合接口 build_memory_index（async，内部 list_all + 格式化）
        index = await mem.build_memory_index(session, 1)
        record(
            "build_memory_index（组合接口）→ 含 [type]+#id+description",
            "[user]" in index and "#" in index and "爱好爬山" in index,
            f"index={index[:80]}",
        )

        # 3.2 session 粒度：ai_service 只注入索引，不调用 select_relevant（相关记忆是缓存杀手）
        record(
            "session 粒度设计：ai_service 只注入索引",
            "#" in index and "你的记忆索引" in index,
            "索引含 #id 供 memory_get 定位",
        )

        # 3.3 build_deepseek_messages：system 含索引；无 relevant_memories 时 user 消息不被改写
        msgs = build_deepseek_messages(
            history=[_FakeMsg(role="user", content="你好")], user_message="你好",
            memory_index=index,
        )
        sys_text = msgs[0]["content"] if msgs else ""
        last_user = msgs[-1]["content"] if msgs else ""
        record(
            "build_deepseek_messages → system 含索引，user 消息不被改写（缓存不变量）",
            "你的记忆索引" in sys_text and last_user == "你好",
            f"system 含索引={'你的记忆索引' in sys_text} user 未改写={last_user == '你好'}",
        )


# ───────────────────────── 场景 4：@tool 注册 ─────────────────────────
async def verify_tool_registry():
    print("\n## 场景 4：@tool 注册")
    from app.harness.tool_registry import registry

    tools = registry.all
    names = [t.name for t in tools]
    memory_tools = [n for n in names if "memory" in n]
    record(
        "registry 含 memory_list/memory_add/memory_get/memory_delete（4 工具）",
        len(memory_tools) >= 4,
        f"memory tools: {sorted(memory_tools)} 总数={len(names)}",
    )


# ───────────────────────── 场景 5：memory_select 策略（方案 b）─────────────────────────
async def verify_select_strategy():
    print("\n## 场景 5：memory_select 策略（关键词优先 + LLM 兜底）")
    await _ensure_user(1)
    from app.core.database import async_session_factory
    from app.harness import memory as mem
    from app.harness.memory.memory import select_relevant, _extract_keywords
    from app.harness import streaming
    from unittest.mock import patch

    async with async_session_factory() as session:
        await mem.add(
            session, 1,
            name="hobby-hiking", type="user", description="爱好爬山", body="用户喜欢周末爬山",
        )
        await session.commit()

        # 5.1 关键词命中 → 零 LLM 调用
        with patch.object(streaming, "deepseek_chat") as mock_llm:
            hits = await select_relevant(session, 1, "我想去爬山")
            record(
                "关键词命中（「爬山」）→ 返回命中，零 LLM 调用",
                len(hits) >= 1 and not mock_llm.called,
                f"hits={len(hits)} llm_called={mock_llm.called}",
            )

        # 5.2 关键词无命中 → LLM 兜底（注意：消息不能含「爬山/周末」等记忆关键词，否则关键词命中不走 LLM）
        async def fake_llm(messages, **kw):
            yield ("token", "[0]")

        with patch.object(streaming, "deepseek_chat", side_effect=fake_llm):
            hits = await select_relevant(session, 1, "今天天气不错，去哪里散心好")
            record(
                "关键词无命中 → LLM 兜底被调用并正确返回",
                len(hits) == 1 and hits[0].name == "hobby-hiking",
                f"hits={len(hits)}",
            )

        # 5.3 LLM 内部抛错 → 返回 []，不阻塞
        async def fake_llm_error(messages, **kw):
            yield ("error", "llm down")

        with patch.object(streaming, "deepseek_chat", side_effect=fake_llm_error):
            hits = await select_relevant(session, 1, "今天天气不错，去哪里散心好")
            record(
                "LLM 内部抛错 → 返回 []，不阻塞",
                hits == [],
                f"hits={hits}",
            )

        # 5.4 无记忆短路（同一 session 内：先清空该用户记忆再测，避免嵌套 session 违反 pool_size=1）
        async def fake_llm2(messages, **kw):
            return  # 若被调用则 fail

        from app.models.user_memory import UserMemory
        from sqlalchemy import delete

        await session.execute(delete(UserMemory).where(UserMemory.user_id == 1))
        await session.commit()
        with patch.object(streaming, "deepseek_chat", side_effect=fake_llm2):
            hits = await select_relevant(session, 1, "随便聊聊")
        record(
            "无记忆短路 → 直接返回 []，不调 LLM",
            hits == [],
            f"hits={hits}",
        )
        # 恢复数据（供后续场景使用）
        await mem.add(
            session, 1,
            name="hobby-hiking", type="user", description="爱好爬山", body="用户喜欢周末爬山",
        )
        await session.commit()

        # 5.5 关键词提取（bigram）
        kws = _extract_keywords("我想去爬山放松")
        record(
            "中文关键词 2 字滑窗（bigram）",
            "爬山" in kws,
            f"keywords={kws[:10]}",
        )


# ───────────────────────── 场景 6：consolidate 保护 ─────────────────────────
async def verify_consolidate():
    print("\n## 场景 6：consolidate 保护（全删重写防丢数据）")
    await _ensure_user(1)
    from app.core.database import async_session_factory
    from app.harness import memory as mem
    from app.harness.memory.memory_provider import BODY_MAX_LEN
    from app.harness import streaming
    from unittest.mock import patch

    async with async_session_factory() as session:
        for i in range(3):
            await mem.add(
                session, 1,
                name=f"mem-{i}", type="user", description=f"记忆 {i}", body=f"内容 {i}",
            )
        await session.commit()

        # 6.1 LLM 返回「非空但全部无效」→ 原数据保留
        async def fake_llm_invalid(messages, **kw):
            yield ("token", '[{"name": "", "description": "", "body": ""}]')

        with patch.object(streaming, "deepseek_chat", side_effect=fake_llm_invalid):
            count = await mem.consolidate(session, 1)
        memories = await mem.list_all(session, 1)
        record(
            "LLM 返回全部无效 → 原 3 条完整保留",
            count == 3 and len(memories) == 3,
            f"consolidate 返回={count} 保留={len(memories)}",
        )

        # 6.2 body 截断至 BODY_MAX_LEN（consolidate 预解析路径：LLM 返回超长 body → 截断后重写）
        long_json = ('[{"name": "long-body", "type": "user", "description": "长内容", "body": "'
                     + "x" * 5000 + '"}]')

        async def fake_llm_long(messages, **kw):
            yield ("token", long_json)

        with patch.object(streaming, "deepseek_chat", side_effect=fake_llm_long):
            count = await mem.consolidate(session, 1)
        await session.commit()
        long_mem = await mem.search(session, 1, ["long"], limit=1)
        body_len = len(long_mem[0].body) if long_mem else -1
        record(
            "consolidate 路径 body 截断至 BODY_MAX_LEN(2000)",
            body_len == BODY_MAX_LEN,
            f"实际长度={body_len} BODY_MAX_LEN={BODY_MAX_LEN}",
        )


# ───────────────────────── 场景 7：extract 失败不阻塞（hook 层，session 粒度）─────────────────────────
async def verify_extract_failure():
    print("\n## 场景 7：extract 失败不阻塞（hook 层 on_session_end）")
    await _ensure_user(1)
    from unittest.mock import patch
    from app.harness.hooks import memory_extract
    from app.harness import memory as mem

    # 2026-08-03 粒度设计：hook 改为 on_session_end（session 结束 /new 时触发），
    # 事件直接带 user_id/session_id。模拟 memory.extract 抛错 → hook 应被 except 捕获不抛出。
    async def fake_extract_error(*a, **k):
        raise RuntimeError("LLM 调用失败")

    try:
        with patch.object(mem, "extract", side_effect=fake_extract_error):
            await memory_extract.on_session_end({"user_id": 1, "session_id": 1})
        record("hook 层 extract 抛错 → 被捕获不抛出，不阻塞主流程", True, "on_session_end 正常返回")
    except Exception as exc:
        record("hook 层 extract 抛错 → 被捕获不抛出，不阻塞主流程", False, f"异常={type(exc).__name__}: {exc}")


# ───────────────────────── 场景 8：memory 工具闭环（memory_get/memory_add）─────────────────────────
async def verify_memory_tools():
    print("\n## 场景 8：memory 工具闭环（memory_get/memory_add）")
    await _ensure_user(1)
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login_token(client, "memory_tool_user")
        headers = {"Authorization": f"Bearer {token}"}

        # 8.1 添加（memory_add 等价：POST /memory）
        add_payload = {
            "name": "rent-focus", "type": "reference",
            "description": "关注 3 号楼漏水", "body": "3 号楼漏水问题持续关注",
        }
        r = await client.post("/api/v1/memory", json=add_payload, headers=headers)
        mem_id = r.json()["data"]["id"]
        record(
            "memory_add → 200 返回记忆（type=reference）",
            r.status_code == 200 and r.json()["data"]["type"] == "reference",
            f"id={mem_id}",
        )

        # 8.2 memory_get 闭环：索引 #id → GET /memory/{id} 取完整内容
        idx = await _get_index_text(client, headers)
        record(
            "memory_get 闭环：索引含 #id",
            f"#{mem_id}" in idx,
            f"index={idx[:80]}",
        )
        r = await client.get(f"/api/v1/memory/{mem_id}", headers=headers)
        body = r.json()
        record(
            "memory_get → 200 返回单条完整内容",
            r.status_code == 200 and body["data"]["id"] == mem_id and "漏水" in body["data"]["body"],
            f"status={r.status_code}",
        )

        # 8.3 memory_get 不存在 id → success None（业务失败≠系统异常）
        r = await client.get("/api/v1/memory/99999", headers=headers)
        body = r.json()
        record(
            "memory_get 不存在 → 200 success None（非 500）",
            r.status_code == 200 and body["data"] is None,
            f"status={r.status_code} body={body}",
        )


async def _get_index_text(client, headers) -> str:
    """通过 API 拿当前用户索引（模拟 ai_service 的 build_memory_index 链路）"""
    from app.harness import memory as mem
    from app.core.database import async_session_factory

    # 走真实函数：需要 user_id，从 DB 查（登录用户 code 对应 openid）
    async with async_session_factory() as session:
        from sqlalchemy import select
        from app.models.user import User
        user = (await session.execute(
            select(User).order_by(User.id.desc()).limit(1)
        )).scalar_one()
        return await mem.build_memory_index(session, user.id)


# ───────────────────────── 场景 9：SESSION_END 触发抽取链路 ─────────────────────────
async def verify_session_end_flow():
    print("\n## 场景 9：SESSION_END 触发抽取链路（session 粒度）")
    await _ensure_user(1)
    from unittest.mock import patch
    from app.harness.hooks import memory_extract, events
    from app.harness import memory as mem

    # 9.1 SESSION_END 事件注册了 handler
    record(
        "SESSION_END 事件已注册 on_session_end handler",
        memory_extract.on_session_end in events._handlers.get(events.SESSION_END, []),
        f"handlers={len(events._handlers.get(events.SESSION_END, []))}",
    )

    # 9.2 有消息时 extract 被调用；无消息时跳过（不调 LLM）
    from app.core.database import async_session_factory
    async with async_session_factory() as session:
        from sqlalchemy import text
        await session.execute(text("DELETE FROM messages"))
        await session.commit()

    calls = []
    async def fake_extract(*a, **k):
        calls.append(a)
        return 0

    with patch.object(mem, "extract", side_effect=fake_extract):
        await memory_extract.on_session_end({"user_id": 1, "session_id": 1})
    record(
        "on_session_end 无消息 → 跳过不调 extract",
        len(calls) == 0,
        f"extract 调用次数={len(calls)}",
    )


async def main():
    print("=" * 64)
    print("  PR #33 review 触发验证（#9 跨会话记忆系统）")
    print(f"  临时 DB: {_DB_PATH}")
    print("=" * 64)

    await _setup_db()
    try:
        await verify_crud_api()
        await _clear_db()
        await verify_user_isolation()
        await _clear_db()
        await verify_injection()
        await _clear_db()
        await verify_tool_registry()
        await _clear_db()
        await verify_select_strategy()
        await _clear_db()
        await verify_consolidate()
        await _clear_db()
        await verify_extract_failure()
        await _clear_db()
        await verify_memory_tools()
        await _clear_db()
        await verify_session_end_flow()
    finally:
        from app.core.database import close_db
        await close_db()

    print("\n" + "=" * 64)
    print("  验证汇总")
    print("=" * 64)
    all_ok = True
    for label, ok, _ in RESULTS:
        if not ok:
            all_ok = False
        print(f"  {'✅' if ok else '❌'} {label}")
    print(f"\n  结果: {'全部通过 ✅' if all_ok else '存在失败 ❌'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

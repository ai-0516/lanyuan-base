"""v2 M3 集成验证：MySQL PersistenceBackend + get-or-load-or-create（TECH_SPEC §5）

验证点（issue #90 验收）：
1. MySQL 三表落库（sessions = header 物化 + incarnation/revision；events = 事件日志；
   persistence_state = store 身份）
2. 同 session 多请求复用（turn 递增正常）
3. 崩溃/重启后恢复：close runtime → 同一 session id → 完整上下文（含首轮内容，
   「地暖 22°C 类历史」验收语义）
4. 身份查询链路（§6.3 M3）：session_id 纯 uuid → FastAPI 内部端点查 owner →
   注入工具调用 _meta（无映射 fail-closed 拒绝）

运行（DATABASE_URL 必须指向 MySQL——v2 会话表是 MySQL 三表）：
  unset DSH_SESSION_ROOT DSH_HOME DSH_CWD
  export DATABASE_URL=mysql+aiomysql://lanyuan_test:lanyuan_test_pw_2026@127.0.0.1:3306/lanyuan_test
  export DEEPSEEK_API_KEY=***
  .venv/bin/python scripts/verify_v2_m3.py
（verify 起 FastAPI 承载 /mcp + 内部端点；lifespan 预热 dsh_runtime，复用其单例）
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# ⚠️ 在 import app.* 之前设置（settings 读取 env 建 engine）
_MYSQL_URL = os.environ.get(
    "DATABASE_URL",
    "mysql+aiomysql://lanyuan_test:lanyuan_test_pw_2026@127.0.0.1:3306/lanyuan_test",
)
os.environ["DATABASE_URL"] = _MYSQL_URL

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from sqlalchemy import text  # noqa: E402

from app.ai.session_service import new_session_id, record_session_owner  # noqa: E402
from app.core.database import async_session_factory  # noqa: E402

USER_ID = 1


async def init_and_ensure_user() -> None:
    """建 v1 业务表（users 等，init_db create_all）并幂等插入测试用户。

    必须与后续 DB 操作同一 event loop（SQLAlchemy async engine 绑定 loop）。
    """
    from app.core.database import init_db

    await init_db()
    from app.models.user import User
    from sqlalchemy import select

    async with async_session_factory() as db:
        exists = (await db.execute(select(User).where(User.id == USER_ID))).scalar_one_or_none()
        if exists is None:
            db.add(User(id=USER_ID, openid=f"v2-verify-{USER_ID}", nickname="验证用户"))
            await db.commit()
            print(f"[info] 已插入测试用户 id={USER_ID}")
        else:
            print(f"[info] 测试用户已存在 id={USER_ID}")


async def check_mysql_tables(session_id: str) -> None:
    """验证 MySQL 三表落库（§8.2）。"""
    async with async_session_factory() as db:
        sessions = (await db.execute(
            text("SELECT id, incarnation, revision, owner_user_id FROM sessions WHERE id = :sid"),
            {"sid": session_id},
        )).first()
        assert sessions is not None, "sessions 表无行（appendBatch 未物化 header？）"
        sid, incarnation, revision, owner = sessions
        assert sid == session_id
        assert incarnation, "incarnation 为空"
        assert revision >= 1, f"revision 应 >= 1，实际 {revision}"
        assert owner == USER_ID, f"owner_user_id 应为 {USER_ID}，实际 {owner}"

        event_count = (await db.execute(
            text("SELECT COUNT(*) FROM events WHERE session_id = :sid"),
            {"sid": session_id},
        )).scalar_one()
        assert event_count >= 1, "events 表无事件"
        print(f"[mysql] sessions: incarnation={incarnation[:8]}… revision={revision} owner={owner}")
        print(f"[mysql] events: {event_count} 条事件落库")
        assert (await db.execute(text("SELECT COUNT(*) FROM persistence_state"))).scalar_one() == 1


async def main() -> None:
    # ⚠️ 先 import app.main：业务 model（users 等）在 import 时注册进
    # Base.metadata——init_db 的 create_all 依赖它们（否则 metadata 空建不出表）
    from app.main import app

    await init_and_ensure_user()
    print(f"[info] DATABASE_URL={_MYSQL_URL.split('@')[-1]}")
    print(f"[info] cordis: {Path(__file__).resolve().parents[2] / 'dsh' / 'cordis-lanyuan.yml'}")

    # 起 FastAPI（挂载 /mcp + /api/internal，§6.2/§6.3）。⚠️ 与 verify 同一
    # event loop（aiomysql engine 绑定 loop，uvicorn 线程会跨 loop 炸）——
    # 用 create_task(server.serve()) 而非独立线程；dsh_runtime.run 走
    # to_thread，run 期间 loop 空闲，DSH 桥才能连上 /mcp + 内部端点。
    import urllib.request

    import uvicorn

    mcp_port = int(os.environ.get("VERIFY_MCP_PORT", "8765"))
    os.environ["LANYUAN_MCP_URL"] = f"http://127.0.0.1:{mcp_port}/mcp/"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=mcp_port,
                                           log_level="warning"))
    server_task = asyncio.create_task(server.serve())
    for _ in range(40):
        try:
            await asyncio.to_thread(
                lambda: urllib.request.urlopen(f"http://127.0.0.1:{mcp_port}/api/health", timeout=1),
            )
            print(f"[info] FastAPI 就绪（/mcp + 内部端点，port={mcp_port}）")
            break
        except Exception:
            await asyncio.sleep(0.5)
    else:
        raise RuntimeError("FastAPI 启动超时")

    from app.ai.dsh_runtime import dsh_runtime

    # M3：session id = 纯 uuid（§6.3，不再编码 user_id）
    session_id = new_session_id()
    print(f"[info] session_id: {session_id}（纯 uuid，owner 映射写 sessions 表）")

    # 模拟 chat 请求：FastAPI 身份权威写 owner 映射（幂等）
    async with async_session_factory() as db:
        await record_session_owner(db, session_id, USER_ID)

    turn_counts: list[int] = []

    async def run_turn(prompt: str, label: str):
        forwarded: list[dict] = []

        def on_notification(n) -> None:
            if n.method == "session.event":
                event = n.payload.get("event") or {}
                forwarded.append(event)

        # to_thread：阻塞期间 event loop 空闲，uvicorn 才能响应 DSH 桥的
        # /mcp 与内部端点请求（工具调用链路依赖）
        result = await asyncio.to_thread(dsh_runtime.run, prompt, session_id, on_notification)
        turns: list[int] = []
        for e in forwarded:
            if e.get("type") in ("turn/start", "turn/end"):
                turn_val = (e.get("data") or {}).get("turn")
                if isinstance(turn_val, int):
                    turns.append(turn_val)
        turn_counts.append(max(turns) if turns else 0)
        print(f"[{label}] finish_reason={result.finish_reason} turn={turn_counts[-1]}")
        return result

    try:
        # ── ① 首轮（新建 session）──
        r1 = await run_turn("记住我的名字：我叫验证用户。请简短回复。", "turn1")
        assert r1.finish_reason == "completed", f"turn1 finish_reason={r1.finish_reason}"

        # ── ② 第二轮（同 session 复用，内存续写）──
        r2 = await run_turn("我刚才说我的名字是什么？", "turn2")
        assert r2.finish_reason == "completed"
        assert turn_counts[1] > turn_counts[0], f"turn 未递增: {turn_counts}"
        print(f"[verify] 同 session 复用：turn {turn_counts[0]} → {turn_counts[1]} ✓")

        # ── ③ 崩溃重启恢复（close → 同一 session id → 从 MySQL load）──
        print("[info] 模拟崩溃重启：close runtime…")
        dsh_runtime.close()
        r3 = await run_turn("我刚才说我的名字是什么？", "turn3(重启后)")
        assert r3.finish_reason == "completed"
        assert "验证用户" in (r3.final_response or ""), \
            "重启后上下文未恢复（未提到「验证用户」）：" + repr(r3.final_response)
        assert turn_counts[2] > turn_counts[1], f"重启后 turn 未递增: {turn_counts}"
        print("[verify] 崩溃/重启后恢复：同一 session id → 完整上下文（提及首轮内容）✓")

        # ── ④ MySQL 三表落库 ──
        await check_mysql_tables(session_id)

        # ── ⑤ 身份查询链路：工具调用注入 owner（内部端点 → _meta.user_id）──
        r4 = await run_turn(
            "用户想了解自己的账号信息：请获取他的基本资料（get_my_profile），然后简单总结两句。",
            "turn4(工具)",
        )
        assert r4.finish_reason == "completed"
        tool_calls = [e for e in r4.events if e.get("type") == "tool/call"]
        tool_results = [e for e in r4.events if e.get("type") == "tool/result"]
        names = [(tc.get("data") or {}).get("name", "?") for tc in tool_calls]
        errors = [e for e in tool_results if (e.get("data") or {}).get("isError")]
        assert any(n == "mcp__lanyuan__get_my_profile" for n in names), f"未调用 get_my_profile: {names}"
        assert not errors, f"工具失败（身份查询/注入链路问题）: {errors}"
        print(f"[verify] 身份查询链路：session_id（纯 uuid）→ 内部端点 → _meta.user_id={USER_ID} ✓")

        print("\n✅ M3 会话验证全部通过（MySQL 落库 / 会话复用 / 崩溃恢复 / 身份查询）")
    finally:
        dsh_runtime.close()
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())

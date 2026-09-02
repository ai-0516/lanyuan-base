"""v2 M3 集成验证：MySQL PersistenceBackend + get-or-load-or-create（TECH_SPEC §5）

验证点（issue #90 验收）：
1. MySQL 三表落库（sessions = header 物化 + incarnation/revision；events = 事件日志；
   persistence_state = store 身份；表由 alembic migration 建，PR #97 review 定案）
2. 同 session 多请求复用（turn 递增正常）
3. 崩溃/重启后恢复：close runtime → 同一 session id → 完整上下文（含首轮内容，
   「地暖 22°C 类历史」验收语义）
4. 身份查询链路（§6.3 M3）：session_id 纯 uuid → FastAPI 内部端点
   GET /api/v2/internal/sessions/{id}/owner 查 owner → 注入工具调用 _meta
   （无映射 fail-closed 拒绝）
5. 会话统一创建点（PR #97 review 定案）：get_or_create_session_v2（复用最近
   会话或新建 + owner 映射）→ DSH 首次对话 resume 空 session 正常物化 agent
6. chat 归属校验 HTTP 端到端（PR #97 dev-lead 第三轮建议）：真实 FastAPI 进程 +
   真实 MySQL owner 映射——owner 本人 200 放行 / 他人 403 / 无映射 403
   （HTTP POST /api/v2/ai/chat，不再是 dsh_runtime.run 直调绕过入口）
7. M4 历史列表端点（PR #98 dev-lead review 建议）：真实 MySQL 投影路径防回归——
   GET /api/v2/ai/session/{id}/messages owner 200 + 投影非空 / 他人 403

运行（DATABASE_URL 必须指向 MySQL——v2 会话表是 MySQL 三表）：
  bash scripts/verify_v2_m3.sh   # 从 gateway 进程自动取 DEEPSEEK_API_KEY
（等价手动：DATABASE_URL=... DEEPSEEK_API_KEY=... .venv/bin/python scripts/verify_v2_m3.py）
（verify 起 FastAPI 承载 /mcp + 内部端点；lifespan 预热 dsh_runtime，复用其单例）
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

# ⚠️ 在 import app.* 之前设置（settings 读取 env 建 engine）
# 测试库凭据不进 git（dev-lead review）：DATABASE_URL 必须由环境注入
# （verify_v2_m3.sh 用 LANYUAN_TEST_MYSQL_PASSWORD 拼装），缺失 fail-fast
_MYSQL_URL = os.environ.get("DATABASE_URL")
if _MYSQL_URL is None:
    raise SystemExit(
        "未设置 DATABASE_URL（v2 会话三表是 MySQL 结构，必须指向 MySQL 测试库）。\n"
        "请用 bash scripts/verify_v2_m3.sh 运行（自动从 LANYUAN_TEST_MYSQL_PASSWORD 拼装），"
        "或手动 export DATABASE_URL=mysql+aiomysql://user:pass@host:port/lanyuan_test"
    )
os.environ["DATABASE_URL"] = _MYSQL_URL

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from sqlalchemy import text  # noqa: E402

from app.services.ai_service import get_or_create_session_v2  # noqa: E402
from app.core.database import async_session_factory  # noqa: E402

USER_ID = 1


def run_alembic_upgrade() -> None:
    """alembic upgrade head（v2 会话三表，§8.2）。进程内调用
    （DATABASE_URL 已指向 MySQL 测试库，env.py 进程 env 优先）。

    幂等策略：
    - 库已在 head → 跳过（alembic_version 匹配当前 head）
    - 否则先 stamp 到 v1 head（5a1b2c3d4e5f）：lanyuan_test 库的 v1 表由
      init_db create_all 管理（历史，无 alembic_version），不 stamp 会撞
      initial_schema 的 CREATE TABLE；stamp 后 upgrade 只跑 v2 增量。
    """
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text

    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    head = ScriptDirectory.from_config(cfg).get_heads()[0]

    sync_url = _MYSQL_URL.replace("mysql+aiomysql", "mysql+pymysql")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            if engine.dialect.has_table(conn, "alembic_version"):
                current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            else:
                current = None
    finally:
        engine.dispose()
    if current == head:
        return  # 已最新（幂等，重复运行安全）

    command.stamp(cfg, "5a1b2c3d4e5f")
    command.upgrade(cfg, "head")


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


async def check_chat_ownership_http(session_id: str, port: int) -> None:
    """⑥ chat 归属校验 HTTP 端到端（PR #97 dev-lead 第三轮建议）。

    真实 FastAPI 进程（uvicorn:port）+ 真实 MySQL owner 映射：
    - owner 本人（USER_ID）→ 200 放行：归属校验在 chat handler 内、
      StreamingResponse 返回**之前**完成（HTTP 200 本身即放行证明）；随即
      关闭连接不消费流，避免多跑一轮完整对话（_stream_chat finally 会
      cancel 后台 run_task，to_thread 线程自身跑完、结果丢弃，无副作用）
    - 他人（USER_ID+1）→ 403（进流前拒绝，零 LLM 成本）
    - 无映射（随机 uuid）→ 403
    """
    import httpx

    from app.core.security import create_access_token

    url = f"http://127.0.0.1:{port}/api/v2/ai/chat"
    owner_headers = {"Authorization": f"Bearer {create_access_token(USER_ID)}"}
    other_headers = {"Authorization": f"Bearer {create_access_token(USER_ID + 1)}"}

    async with httpx.AsyncClient(timeout=60) as client:
        # ① owner 本人 → 200 放行：归属校验在 chat handler 内、
        #    StreamingResponse 返回**之前**完成（HTTP 200 本身即放行证明）。
        #    消费完整 SSE 流到 turn/end：一是顺带验证真实对话链路走 HTTP 入口
        #    正常（不再 dsh_runtime.run 直调），二是避免提前关闭连接导致
        #    _stream_chat finally cancel 掉 to_thread 的 DSH run——线程不可
        #    中断会继续跑完整对话，在 server 关闭后打 /mcp 产生幽灵 500 噪音
        seen_turn_end = False
        async with client.stream(
            "POST", url, headers=owner_headers,
            json={"message": "请回复：OK", "session_id": session_id},
        ) as resp:
            assert resp.status_code == 200, f"owner 本人应 200 放行，实际 {resp.status_code}"
            assert resp.headers.get("content-type", "").startswith("text/event-stream")
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and '"type": "turn/end"' in line:
                    seen_turn_end = True
                    break
        assert seen_turn_end, "HTTP chat 流未收到 turn/end（对话未正常完成）"
        print("[verify] chat 归属校验 HTTP：owner 本人 → 200 放行 + 完整对话链路 ✓")

        resp_other = await client.post(
            url, headers=other_headers,
            json={"message": "hi", "session_id": session_id},
        )
        assert resp_other.status_code == 403, f"他人应 403，实际 {resp_other.status_code}"

        resp_none = await client.post(
            url, headers=owner_headers,
            json={"message": "hi", "session_id": str(uuid.uuid4())},
        )
        assert resp_none.status_code == 403, f"无映射应 403，实际 {resp_none.status_code}"
    print("[verify] chat 归属校验 HTTP：他人 403 / 无映射 403 ✓")


async def check_messages_endpoint(session_id: str, port: int) -> None:
    """⑦ M4 历史列表端点真实 MySQL 路径（PR #98 review 建议：防回归）。

    前 6 步已在 events 表落真实对话事件 → GET /messages 走真实 SQL
    （turn/start 窗口 + 事件窗口 + 投影）：
    - owner 本人 → 200 + 投影消息非空 + 契约字段（messages/has_more/cursor）
    - 他人 → 403（归属校验）
    """
    import httpx

    from app.core.security import create_access_token

    url = f"http://127.0.0.1:{port}/api/v2/ai/session/{session_id}/messages"
    owner_headers = {"Authorization": f"Bearer {create_access_token(USER_ID)}"}
    other_headers = {"Authorization": f"Bearer {create_access_token(USER_ID + 1)}"}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=owner_headers)
        assert resp.status_code == 200, f"owner 本人应 200，实际 {resp.status_code}"
        body = resp.json()
        assert isinstance(body.get("messages"), list) and body["messages"], \
            "真实 MySQL 投影消息为空（前 6 步对话事件未投影出来？）"
        # 契约字段：倒序（最新在前）+ has_more bool + cursor int
        assert body["messages"][0]["role"] == "assistant"
        assert isinstance(body.get("has_more"), bool)
        assert isinstance(body.get("cursor"), int)
        print(f"[verify] messages 端点真实 MySQL：owner 200，投影 {len(body['messages'])} 条 ✓")

        resp_other = await client.get(url, headers=other_headers)
        assert resp_other.status_code == 403, f"他人应 403，实际 {resp_other.status_code}"
    print("[verify] messages 端点真实 MySQL：他人 403 ✓")


async def main() -> None:
    # ⚠️ 先 import app.main：业务 model（users 等）在 import 时注册进
    # Base.metadata——init_db 的 create_all 依赖它们（否则 metadata 空建不出表）
    from app.main import app

    # v2 会话三表由 alembic 管理（PR #97 review 定案：表结构真源 = migration，
    # DSH 插件不再自建表）——verify 前先 upgrade head 建表（DATABASE_URL 已指
    # lanyuan_test；alembic env.py 支持进程 env 优先）
    await asyncio.to_thread(run_alembic_upgrade)

    await init_and_ensure_user()
    print(f"[info] DATABASE_URL={_MYSQL_URL.split('@')[-1]}")
    print(f"[info] cordis: {Path(__file__).resolve().parents[2] / 'dsh' / 'cordis-lanyuan.yml'}")

    # 起 FastAPI（挂载 /mcp + /api/v2/internal，§6.2/§6.3）。⚠️ 与 verify 同一
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

    # M3（PR #97 review 定案）：session 统一创建点 = get_or_create_session_v2
    # （模拟前端先调 POST /api/v2/ai/session；复用最近会话或新建 + owner 映射）
    async with async_session_factory() as db:
        session_id = await get_or_create_session_v2(db, USER_ID)
    print(f"[info] session_id: {session_id}（纯 uuid，get_or_create_session_v2 统一创建）")

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

        # ── ⑥ chat 归属校验 HTTP 端到端（dev-lead 第三轮建议）──
        # 前 5 步的对话都走 dsh_runtime.run 直调（绕过 HTTP 入口），本步补齐
        # 真实 HTTP POST /api/v2/ai/chat 的归属校验断言（200/403/403）
        await check_chat_ownership_http(session_id, mcp_port)

        # ── ⑦ M4 历史列表端点（PR #98 review 建议）：真实 MySQL 投影路径 ──
        # 前 6 步已在 events 表落真实对话事件 → GET /messages 走真实 SQL
        await check_messages_endpoint(session_id, mcp_port)

        print("\n✅ M3 会话验证全部通过（MySQL 落库 / 会话复用 / 崩溃恢复 / "
              "身份查询 / chat 归属校验 / M4 messages 端点）")
    finally:
        dsh_runtime.close()
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())

"""M2 全链路验证：业务工具桥（TECH_SPEC §6，streamable-http 挂载 FastAPI）

验证点：
1. FastAPI 启动并挂载 /mcp（MCP server 随 FastAPI 生命周期，§6.2）
2. agent 真实调用业务工具（mcp__lanyuan__get_my_profile，§6.4b @mcp_tool 原生注册）
3. user_id 注入链路通（session id `v2-{user_id}-{uuid}` → 桥插件 _meta → MCP
   server，HTTP 传输透传 _meta）——无 _meta 必抛 PermissionError，成功即证明注入通
4. 事件层：tool/call、tool/result 在后端事件流可见（白名单外不转发前端）

运行：unset DSH_SESSION_ROOT DSH_HOME DSH_CWD && export DEEPSEEK_API_KEY=***
      .venv/bin/python scripts/verify_v2_tools.py
（verify 起 FastAPI 承载 MCP server；lifespan 预热 dsh_runtime，verify 复用其单例）
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from sqlalchemy import select  # noqa: E402

from app.ai.dsh_runtime import DSH_DIR  # noqa: E402
from app.core.database import async_session_factory  # noqa: E402
from app.models.user import User  # noqa: E402

USER_ID = 1
USER_NICKNAME = "验证用户"


def ensure_test_user() -> None:
    """幂等插入测试用户（开发库无生产数据；工具侧 user_id 校验依赖真实用户存在）"""

    async def _ensure() -> None:
        async with async_session_factory() as db:
            exists = (await db.execute(select(User).where(User.id == USER_ID))).scalar_one_or_none()
            if exists is None:
                db.add(User(id=USER_ID, openid=f"v2-verify-{USER_ID}", nickname=USER_NICKNAME))
                await db.commit()
                print(f"[info] 已插入测试用户 id={USER_ID}")
            else:
                print(f"[info] 测试用户已存在 id={USER_ID}")

    asyncio.run(_ensure())


def main() -> None:
    ensure_test_user()
    print(f"[info] runtime_bin: {DSH_DIR / 'bin/dsh-jsonrpc-agent.js'}")
    print(f"[info] cordis: {DSH_DIR / 'cordis-lanyuan.yml'}")

    # 起 FastAPI（挂载 /mcp，streamable-http）承载 MCP server（§6.2）。
    # lifespan 默认 auto：fastmcp 的 StreamableHTTPSessionManager 依赖 lifespan
    # 初始化（lifespan=off 会 500）；lifespan 同时预热 dsh_runtime（§3.1），
    # verify 复用其单例发对话，不自建 harness（避免双 runtime 争用 DSH_HOME）。
    import threading
    import time
    import urllib.request

    import uvicorn

    from app.main import app

    mcp_port = int(os.environ.get("VERIFY_MCP_PORT", "8765"))
    os.environ["LANYUAN_MCP_URL"] = f"http://127.0.0.1:{mcp_port}/mcp/"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=mcp_port,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{mcp_port}/api/health", timeout=1)
            print(f"[info] FastAPI 就绪（/mcp 挂载，port={mcp_port}）")
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError("FastAPI 启动超时")

    # FastAPI lifespan（auto）会预热 dsh_runtime（§3.1）——verify 复用该单例发对话，
    # 不自建 harness（避免两个 DSH runtime 争用 DSH_HOME/sessions）
    from app.ai.dsh_runtime import dsh_runtime

    session_id = f"v2-{USER_ID}-{uuid.uuid4()}"
    try:
        result = dsh_runtime.run(
            "用户想了解自己的账号信息：请获取他的基本资料（get_my_profile），然后简单总结两句。",
            session_id=session_id,
        )
        print(f"[result] finish_reason={result.finish_reason}")
        print(f"[result] final_response={result.final_response!r}")

        tool_calls = [e for e in result.events if e.get("type") == "tool/call"]
        tool_results = [e for e in result.events if e.get("type") == "tool/result"]
        names = [(tc.get("data") or {}).get("name", "?") for tc in tool_calls]
        print(f"[result] tool_calls={len(tool_calls)} names={names}")
        print(f"[result] tool_results={len(tool_results)}")
        for tr in tool_results:
            d = tr.get("data") or {}
            content = d.get("content")
            print(f"  [tool/result] keys={list(d.keys())} content={str(content)[:300]}")

        assert result.finish_reason == "completed", f"finish_reason={result.finish_reason}"
        # @mcp_tool 原生注册（§6.4b）：search_history 不迁移（v2 历史搜索用 DSH
        # session-query），业务工具只有 get_my_profile
        assert any(n == "mcp__lanyuan__get_my_profile" for n in names), f"未调用 get_my_profile: {names}"
        # tool/result 无 isError = 工具成功执行 = MCP server 侧拿到 _meta.user_id
        # （无 _meta 时 MCP server 抛 PermissionError → isError=true）
        errors = [e for e in tool_results if (e.get("data") or {}).get("isError")]
        assert not errors, f"存在工具失败（疑似 user_id 注入失败）: {errors}"
        print("\n✅ M2 工具桥全链路验证通过（工具调用 + user_id 注入链路 OK）")
    finally:
        dsh_runtime.close()


if __name__ == "__main__":
    main()

"""M2 全链路验证：业务工具桥（TECH_SPEC §6）

验证点：
1. 6 插件配置（含 lanyuan-bridge）runtime 正常拉起，MCP server 被 spawn
2. agent 真实调用业务工具（mcp__lanyuan__search_history / get_profile）
3. user_id 注入链路通（session id `v2-{user_id}-{uuid}` → 桥插件 _meta →
   MCP server）——MCP server 无 _meta 必抛 PermissionError，工具成功执行即证明注入通
4. 事件层：tool/call、tool/result 在后端事件流可见（白名单外不转发前端）

运行：unset DSH_SESSION_ROOT DSH_HOME DSH_CWD && export DEEPSEEK_API_KEY=***
      .venv/bin/python scripts/verify_v2_tools.py
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from sqlalchemy import select  # noqa: E402

from app.ai.dsh_runtime import DSH_DIR, _LLM_MODEL, _runtime_env  # noqa: E402
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

    from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

    env = _runtime_env()
    session_id = f"v2-{USER_ID}-{uuid.uuid4()}"

    config = DeepSeekHarnessConfig(
        provider="deepseek-official",
        model=_LLM_MODEL,
        runtime_bin=str(DSH_DIR / "bin" / "dsh-jsonrpc-agent.js"),
        cordis=str(DSH_DIR / "cordis-lanyuan.yml"),
        env=env,
        request_timeout_seconds=180,
    )
    harness = DeepSeekHarness(config)
    harness.start()
    try:
        result = harness.run(
            "用户想回忆过去聊过的事情：请先搜索他的历史对话（search_history），"
            "再获取他的基本资料（get_profile），然后简单总结两句。",
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
        assert any(n.startswith("mcp__lanyuan__") for n in names), f"未调用业务工具: {names}"
        # tool/result 无 isError = 工具成功执行 = MCP server 侧拿到 _meta.user_id
        # （无 _meta 时 MCP server 抛 PermissionError → isError=true）
        errors = [e for e in tool_results if (e.get("data") or {}).get("isError")]
        assert not errors, f"存在工具失败（疑似 user_id 注入失败）: {errors}"
        print("\n✅ M2 工具桥全链路验证通过（工具调用 + user_id 注入链路 OK）")
    finally:
        harness.close()


if __name__ == "__main__":
    main()

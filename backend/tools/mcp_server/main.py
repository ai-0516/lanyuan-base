"""lanyuan 业务工具 MCP server（TECH_SPEC §6）：fastmcp，stdio transport

由 DSH runtime 侧桥插件（@lanyuan/dsh-lanyuan-bridge）spawn，每 worker 常驻一个。
工具注册名经桥插件映射为 mcp__lanyuan__<rawName>（§6.1）。

身份设计（§6.3）：工具签名**不含** user_id 参数（LLM 不可见）；执行时身份来自
桥插件在 callTool 请求 `_meta` 中注入的 user_id（桥层强制绑定，LLM 无法伪造）。
任何工具实现不得信任模型输入中的身份字段——本文件统一从 `_meta` 提取。

工具实现**复用 v1 @tool**（§6.4：业务实现单份，MCP 层只做身份适配）：
import app.api.v1.* 触发 @tool 注册 → registry.get(name) 取 ToolDef → td.execute(db,
user_id, args) 完成 Depends 注入（db/user_id）+ api_success 解包 + result_formatter
删减，返回的是删减后的 JSON 文本（v1 formatter 契约 #69：保留 JSON 结构）→
json.loads 还原为 MCP 结构化返回。

启动：.venv/bin/python backend/tools/mcp_server/main.py（cwd=backend/，桥插件指定）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # backend/

# import 触发 v1 @tool 注册（@tool 装饰器在模块顶层执行，注册进全局 registry）
import app.api.v1.ai  # noqa: E402,F401  # search_history
import app.api.v1.profile  # noqa: E402,F401  # get_my_profile

from fastmcp import FastMCP  # noqa: E402
from fastmcp.server.context import Context  # noqa: E402

from app.core.database import async_session_factory  # noqa: E402
from app.harness.tool_registry import registry  # noqa: E402

mcp = FastMCP("lanyuan")

# ── 身份提取（§6.3：唯一来源 = 桥插件注入的 _meta，拒绝模型输入中的身份字段） ──

_META_ERR = "身份校验失败：桥层未注入 user_id（_meta）"


def _user_id(ctx: Context) -> int:
    """从 callTool 请求的 `_meta` 提取 user_id（桥插件注入；Meta 是 pydantic 模型，extra=allow）"""
    meta = (ctx.request_context.meta if ctx.request_context is not None else None)
    uid = getattr(meta, "user_id", None) if meta is not None else None
    if uid is None:
        raise PermissionError(_META_ERR)
    return int(uid)


# ── v1 工具复用（业务逻辑单份，MCP 层只做身份适配 + 结构化还原） ──


async def _call_v1(tool_name: str, ctx: Context, args: dict) -> dict:
    """调用 v1 @tool 工具：ToolDef.execute 注入 db/user_id + 解包 + formatter 删减。

    v1 formatter 契约（tool_registry.py，#69）：输出是删减后的 JSON 文本（保留 JSON
    结构），json.loads 还原即 MCP 结构化返回——删减（如 avatar/openid/unit/room）
    由 v1 formatter 完成，这里不做二次清洗。
    """
    user_id = _user_id(ctx)
    td = registry.get(tool_name)
    if td is None:
        raise RuntimeError(f"v1 工具未注册: {tool_name}")
    async with async_session_factory() as db:
        text = await td.execute(db, user_id, args)
        return json.loads(text)


@mcp.tool()
async def search_history(
    query: str,
    limit: int = 3,
    window: int = 5,
    sort: str = "relevance",
    ctx: Context = None,  # type: ignore[assignment]  # fastmcp 注入，不进 schema
) -> dict:
    """搜索用户过往对话历史。当用户提到过去聊过的内容、或需要回忆更早
    对话细节时使用。返回命中的消息及其上下文窗口（最多 limit 条命中，
    每条带前后 window 条上下文）。

    语义说明：返回的 total = **合并后片段数**（segment 数）——同一会话内
    窗口重叠的连续命中合并为一个片段（同会话连续命中只返回一条窗口；
    跨会话命中各自独立），因此 total 可能小于实际命中条数。
    """
    return await _call_v1("search_history", ctx, {
        "query": query,
        "limit": limit,
        "window": window,
        "sort": sort,
    })


@mcp.tool()
async def get_profile(ctx: Context = None) -> dict:  # type: ignore[assignment]
    """获取当前用户的基本资料（昵称、小区、楼栋、简介等；房号/头像为隐私不返回）。"""
    return await _call_v1("get_my_profile", ctx, {})


if __name__ == "__main__":
    mcp.run()  # stdio transport

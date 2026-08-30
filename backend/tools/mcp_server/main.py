"""lanyuan 业务工具 MCP server（TECH_SPEC §6）：fastmcp，stdio transport

由 DSH runtime 侧桥插件（@lanyuan/dsh-lanyuan-bridge）spawn，每 worker 常驻一个。
工具注册名经桥插件映射为 mcp__lanyuan__<rawName>（§6.1）。

身份设计（§6.3）：工具签名**不含** user_id 参数（LLM 不可见）；执行时身份来自
桥插件在 callTool 请求 `_meta` 中注入的 user_id（桥层强制绑定，LLM 无法伪造）。
任何工具实现不得信任模型输入中的身份字段——本文件统一从 `_meta` 提取。

工具**自动注册 v1 @tool**（§6.4：业务实现单份，本文件不写工具逻辑）：
import app.api.v1.* 触发 @tool 注册进全局 registry → _make_mcp_tool 按 §6.4
白名单为每个 v1 工具生成 MCP 包装（签名/类型/默认值/docstring 从 v1 函数还原，
schema 与 v1 ToolDef 同源）→ 执行走 _call_v1：td.execute(db, user_id, args)
完成 Depends 注入 + api_success 解包 + result_formatter 删减（输出删减后 JSON
文本，#69 契约保留 JSON 结构）→ json.loads 还原为 MCP 结构化返回。
新增 v1 工具后只需在 _make_mcp_tool 调用处加一行白名单注册。

启动：.venv/bin/python backend/tools/mcp_server/main.py（cwd=backend/，桥插件指定）
"""
from __future__ import annotations

import inspect
import json
import sys
from typing import Any, Callable, get_type_hints
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # backend/

# import 触发 v1 @tool 注册（@tool 装饰器在模块顶层执行，注册进全局 registry）
import app.api.v1.ai  # noqa: E402,F401  # search_history
import app.api.v1.profile  # noqa: E402,F401  # get_my_profile

from fastapi.params import Depends as DependsClass  # noqa: E402
from fastmcp import FastMCP  # noqa: E402
from fastmcp.server.context import Context  # noqa: E402

from app.core.database import async_session_factory  # noqa: E402
from app.harness.tool_registry import ToolDef, registry  # noqa: E402

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


# ── v1 @tool 自动注册（§6.4：业务实现单份，MCP 层只做身份适配 + 结构化还原） ──


async def _call_v1(td: ToolDef, ctx: Context, args: dict) -> dict:
    """调用 v1 @tool 工具：ToolDef.execute 注入 db/user_id + 解包 + formatter 删减。

    v1 formatter 契约（tool_registry.py，#69）：输出是删减后的 JSON 文本（保留 JSON
    结构），json.loads 还原即 MCP 结构化返回——删减（如 avatar/openid/unit/room）
    由 v1 formatter 完成，这里不做二次清洗。
    """
    user_id = _user_id(ctx)
    async with async_session_factory() as db:
        text = await td.execute(db, user_id, args)
        return json.loads(text)


def _make_mcp_tool(mcp_name: str, v1_name: str) -> Callable:
    """为 v1 @tool 生成 MCP 包装函数并注册（§6.4 白名单注册）。

    签名/类型/默认值从 v1 函数签名还原（跳过 Depends 注入参数），docstring 用
    v1 的——MCP schema 与 v1 ToolDef schema 同源，LLM 看到的与 v1 一致。
    注：v1 中 Pydantic model 参数（如 update_my_profile 的 UserUpdate）fastmcp 侧
    为嵌套 object 而非 v1 的字段展平——M2 白名单无此类工具，迁移时再处理。
    """
    td = registry.get(v1_name)
    if td is None:
        raise RuntimeError(f"v1 工具未注册: {v1_name}")

    fn_sig = inspect.signature(td.fn)
    hints = get_type_hints(td.fn)

    async def wrapped(ctx: Context = None, **kwargs: Any) -> dict:  # type: ignore[assignment]
        return await _call_v1(td, ctx, kwargs)

    sig_params: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}
    for pname, param in fn_sig.parameters.items():
        if param.default is not inspect.Parameter.empty and isinstance(param.default, DependsClass):
            continue  # Depends 注入参数（db/user_id）：不暴露给 LLM
        annotation = hints.get(pname, param.annotation)
        if annotation is inspect.Parameter.empty:
            annotation = str
        sig_params.append(inspect.Parameter(
            pname,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=annotation,
            default=param.default if param.default is not inspect.Parameter.empty else inspect.Parameter.empty,
        ))
        annotations[pname] = annotation
    sig_params.append(inspect.Parameter(
        "ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context, default=None,
    ))
    annotations["ctx"] = Context

    wrapped.__name__ = mcp_name
    wrapped.__doc__ = td.description
    wrapped.__signature__ = inspect.Signature(sig_params)  # type: ignore[attr-defined]
    wrapped.__annotations__ = annotations

    mcp.add_tool(wrapped)
    return wrapped


# §6.4 首批工具白名单（MCP 工具名 → v1 注册名；新增 v1 工具迁移时在此加一行）
search_history = _make_mcp_tool("search_history", "search_history")
get_profile = _make_mcp_tool("get_profile", "get_my_profile")


if __name__ == "__main__":
    mcp.run()  # stdio transport

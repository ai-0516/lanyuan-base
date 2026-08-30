"""lanyuan 业务工具 MCP server（TECH_SPEC §6）：fastmcp，stdio transport

由 DSH runtime 侧桥插件（@lanyuan/dsh-lanyuan-bridge）spawn，每 worker 常驻一个。
工具注册名经桥插件映射为 mcp__lanyuan__<rawName>（§6.1）。

身份设计（§6.3）：工具签名**不含** user_id 参数（LLM 不可见）；执行时身份来自
桥插件在 callTool 请求 `_meta` 中注入的 user_id（桥层强制绑定，LLM 无法伪造）。
任何工具实现不得信任模型输入中的身份字段——本文件统一从 `_meta` 提取。

工具**自动注册全部 v1 @tool**（§6.4：endpoint 对 MCP 无感，与 @tool 同语义）：
import app.api.v1.* 触发 @tool 装饰器静默注册进全局 registry（v1 侧零感知）→
本文件遍历 registry.all 自动生成 MCP 包装并注册（main.py 零工具引用）——
MCP 工具名 = v1 注册名（单一真源），新增 v1 @tool 自动进入两个 LLM 消费方。

_make_mcp_tool：签名/类型/默认值从 v1 函数签名还原（跳过 Depends 注入参数
db/user_id；Pydantic model 参数展平为独立字段，对齐 v1 _flatten_model），
docstring 用 v1 的——MCP schema 与 v1 ToolDef schema 同源。

_call_v1：td.execute(db, user_id, args) 完成 Depends 注入 + api_success 解包 +
result_formatter 删减（输出删减后 JSON 文本，#69 契约保留 JSON 结构）→
json.loads 还原为 MCP 结构化返回；db 生命周期对齐 FastAPI get_db（请求级
commit/rollback——v1 工具依赖该契约，写操作才正确落库）。

启动：.venv/bin/python backend/tools/mcp_server/main.py（cwd=backend/，桥插件指定）
"""
from __future__ import annotations

import inspect
import json
import sys
import types as pytypes
from typing import Any, Callable, Union, get_args, get_origin, get_type_hints
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # backend/

# import 触发 v1 @tool 注册（@tool 装饰器在模块顶层执行，注册进全局 registry）
import app.api.v1.ai  # noqa: E402,F401
import app.api.v1.profile  # noqa: E402,F401

from fastapi.params import Depends as DependsClass  # noqa: E402
from fastmcp import FastMCP  # noqa: E402
from fastmcp.server.context import Context  # noqa: E402
from pydantic import BaseModel  # noqa: E402

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


# ── v1 @tool 自动注册（§6.4：endpoint 无感，main.py 零工具引用） ──


async def _call_v1(td: ToolDef, ctx: Context, args: dict) -> dict:
    """调用 v1 @tool 工具（身份适配 + 结构化还原，业务实现全在 v1）。

    - fastmcp 会给未传的可选参数填充默认值（default=None → None）：过滤 None
      还原 v1 调用语义（LLM 不传 = 参数不存在）——update 的 exclude_unset 才正确
    - db 生命周期对齐 FastAPI get_db（请求级 commit/rollback）——v1 工具的
      Depends(get_db) 契约依赖该语义，写操作才正确落库
    - formatter 契约（#69）：输出删减后 JSON 文本（保留 JSON 结构），json.loads
      还原即结构化返回；删减（avatar/openid/unit/room）由 v1 formatter 完成
    """
    user_id = _user_id(ctx)
    args = {k: v for k, v in args.items() if v is not None}
    async with async_session_factory() as db:
        try:
            text = await td.execute(db, user_id, args)
            await db.commit()
            return json.loads(text)
        except Exception:
            await db.rollback()
            raise


def _make_mcp_tool(td: ToolDef) -> Callable:
    """为 v1 @tool 生成 MCP 包装函数并注册（自动发现，v1 侧零感知）。

    签名/类型/默认值从 v1 函数签名还原（跳过 Depends 注入参数 db/user_id），
    Pydantic model 参数展平为独立字段（对齐 v1 ToolDef._build_schema），
    docstring 用 v1 的——MCP schema 与 v1 ToolDef schema 同源。
    MCP 工具名 = v1 注册名（单一真源）。
    """
    fn_sig = inspect.signature(td.fn)
    hints = get_type_hints(td.fn)

    async def wrapped(ctx: Context = None, **kwargs: Any) -> dict:  # type: ignore[assignment]
        return await _call_v1(td, ctx, kwargs)

    sig_required: list[inspect.Parameter] = []
    sig_optional: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}
    seen: set[str] = set()
    for pname, param in fn_sig.parameters.items():
        if param.default is not inspect.Parameter.empty and isinstance(param.default, DependsClass):
            continue  # Depends 注入参数（db/user_id）：不暴露给 LLM
        annotation = hints.get(pname, param.annotation)
        if annotation is inspect.Parameter.empty:
            annotation = str
        # Pydantic model 参数 → 字段展平（v1 同规则：字段直接进 schema，冲突先到先得）
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            for fname, finfo in annotation.model_fields.items():
                if fname in seen:
                    continue
                fanno = finfo.annotation if finfo.annotation is not inspect.Parameter.empty else str
                if finfo.is_required():
                    sig_required.append(inspect.Parameter(
                        fname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=fanno,
                    ))
                else:
                    sig_optional.append(inspect.Parameter(
                        fname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=fanno,
                        default=finfo.default if finfo.default is not inspect.Parameter.empty else None,
                    ))
                annotations[fname] = fanno
                seen.add(fname)
            continue
        if param.default is inspect.Parameter.empty:
            sig_required.append(inspect.Parameter(
                pname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation,
            ))
        else:
            sig_optional.append(inspect.Parameter(
                pname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation, default=param.default,
            ))
        annotations[pname] = annotation
        seen.add(pname)
    sig_optional.append(inspect.Parameter(
        "ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context, default=None,
    ))
    annotations["ctx"] = Context
    # 必填在前（inspect.Signature 语法要求，Python 不允许默认参数后跟必填参数）；
    # 组内保持 v1 签名顺序 → required 列表与 v1 一致
    sig_params = sig_required + sig_optional

    wrapped.__name__ = td.name
    wrapped.__doc__ = td.description
    wrapped.__signature__ = inspect.Signature(sig_params)  # type: ignore[attr-defined]
    wrapped.__annotations__ = annotations

    mcp.add_tool(wrapped)
    return wrapped


# §6.4：自动注册全部 v1 @tool（endpoint 无感，与 @tool 同语义——注册进 registry
# 即对 v1/v2 两个 LLM 消费方可见；MCP 工具名 = v1 注册名）
_MCP_TOOLS: dict[str, Callable] = {}
for _td in registry.all:
    _MCP_TOOLS[_td.name] = _make_mcp_tool(_td)


if __name__ == "__main__":
    mcp.run()  # stdio transport

"""@mcp_tool 装饰器：MCP server 原生工具注册（TECH_SPEC §6.4b，不依赖 v1 @tool）

工具函数 = 纯业务函数 + 注入参数标记（按参数名识别，LLM 不可见）：
- `user_id: int = None` → 由装饰器从 callTool 请求 `_meta` 注入（§6.3 身份强制绑定）
- `db: AsyncSession = None` → 由装饰器注入 async_session_factory 会话
  （请求级 commit/rollback，对齐 FastAPI get_db 契约——写操作正确落库）

其余参数为业务参数，进 MCP schema（类型/默认值从函数签名生成）。
返回值 = 结构化 dict（fastmcp 序列化）；隐私字段由工具自己控制不返回。

与 fastmcp 原生 `@mcp.tool()` 的区别：原生不注入身份/db，每个工具要重复
`_user_id(ctx)` + 会话管理；本装饰器统一处理，工具函数只写业务。

与 v1 `@tool` 的区别：不依赖 FastAPI Depends / api_success / result_formatter——
工具是原生 MCP 形态，v1 工具体系退役后无需迁移（§6.4b）。
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, get_type_hints

from fastmcp import FastMCP
from fastmcp.server.context import Context

from app.core.database import async_session_factory

# MCP server 实例（§6.2：工具注册的真源，挂载到 FastAPI /mcp）
mcp = FastMCP("lanyuan")

# 注入参数（按参数名识别）：不暴露给 LLM，由装饰器运行时注入
_INJECTED_PARAMS = frozenset({"user_id", "db"})

# 已注册的 MCP 包装函数（工具名 → wrapped；测试/调试直接调用验证注入链）
_REGISTERED_TOOLS: dict[str, Callable] = {}


def _user_id_from_meta(ctx: Context) -> int:
    """从 callTool 请求的 `_meta` 提取 user_id（桥插件注入；Meta 是 pydantic 模型，extra=allow）"""
    meta = (ctx.request_context.meta if ctx.request_context is not None else None)
    uid = getattr(meta, "user_id", None) if meta is not None else None
    if uid is None:
        raise PermissionError("身份校验失败：桥层未注入 user_id（_meta）")
    return int(uid)


def mcp_tool(fn: Callable | None = None, *, name: str | None = None) -> Callable:
    """@mcp_tool 装饰器：注册 MCP 业务工具（§6.4b）

    用法：
        @mcp_tool
        async def search_history(query: str, user_id: int = None, db: AsyncSession = None) -> dict: ...

    签名中的 `user_id`/`db` 参数为注入参数（LLM 不可见）；其余参数进 schema。
    返回原函数（无感，同 v1 @tool 语义）。
    """

    def _register(f: Callable) -> Callable:
        tool_name = name or f.__name__
        fn_sig = inspect.signature(f)
        hints = get_type_hints(f)

        # ── 签名 → MCP schema（注入参数剔除；必填在前，组内保持函数顺序） ──
        sig_required: list[inspect.Parameter] = []
        sig_optional: list[inspect.Parameter] = []
        annotations: dict[str, Any] = {}
        for pname, param in fn_sig.parameters.items():
            if pname in _INJECTED_PARAMS:
                continue
            annotation = hints.get(pname, param.annotation)
            if annotation is inspect.Parameter.empty:
                annotation = str
            if param.default is inspect.Parameter.empty:
                sig_required.append(inspect.Parameter(
                    pname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation,
                ))
            else:
                sig_optional.append(inspect.Parameter(
                    pname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation, default=param.default,
                ))
            annotations[pname] = annotation
        sig_optional.append(inspect.Parameter(
            "ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context, default=None,
        ))
        annotations["ctx"] = Context

        # ── 包装执行：_meta 身份 + db 会话（请求级 commit）→ 调业务函数 ──
        async def wrapped(ctx: Context = None, **kwargs: Any) -> Any:  # type: ignore[assignment]
            user_id = _user_id_from_meta(ctx)
            call_kwargs: dict[str, Any] = dict(kwargs)
            async with async_session_factory() as db:
                try:
                    if "user_id" in fn_sig.parameters:
                        call_kwargs["user_id"] = user_id
                    if "db" in fn_sig.parameters:
                        call_kwargs["db"] = db
                    result = await f(**call_kwargs)
                    await db.commit()
                    return result
                except Exception:
                    await db.rollback()
                    raise

        wrapped.__name__ = tool_name
        wrapped.__doc__ = (f.__doc__ or "").strip()
        wrapped.__signature__ = inspect.Signature(sig_required + sig_optional)  # type: ignore[attr-defined]
        wrapped.__annotations__ = annotations

        mcp.add_tool(wrapped)
        _REGISTERED_TOOLS[tool_name] = wrapped
        return f  # 返回原函数（endpoint/其他消费方无感）

    return _register(fn) if fn is not None else _register

"""@mcp_tool 装饰器：MCP server 原生工具注册（TECH_SPEC §6.4b，不依赖 v1 @tool）

**用法与 v1 @tool 一致（2026-08-30 用户定）**：直接写在 v2 endpoint 上——
工具函数就是 FastAPI endpoint（@router 与 @mcp_tool 叠加），注入参数用
`Depends(get_db)` / `Depends(get_current_user)` 声明（LLM 不可见）：

    @router.get("/user/me")
    @mcp_tool
    async def get_my_profile(
        db: AsyncSession = Depends(get_db),
        user_id: int = Depends(get_current_user),
    ):
        \"\"\"业务描述进 MCP schema；db/user_id 是注入参数（LLM 不可见）\"\"\"
        ...

- HTTP 模式：FastAPI Depends 正常解析（装饰器返回原函数，endpoint 无感）
- MCP 模式：user_id 来自 callTool 请求 `_meta`（§6.3 身份强制绑定，桥层注入；
  无 `_meta` → PermissionError 拒绝，fail-closed）；db 由装饰器注入
  async_session_factory 会话（请求级 commit/rollback，对齐 get_db 契约）
- 返回 `api_success(...)`（统一响应格式，HTTP 消费）；MCP 模式自动解包 data
  （LLM 看到结构化 dict，同 v1 ToolDef.execute 语义）

与 fastmcp 原生 `@mcp.tool()` 的区别：原生不注入身份/db，每个工具要重复
`_user_id(ctx)` + 会话管理；本装饰器统一处理，工具函数只写业务。
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, get_type_hints

from fastapi.params import Depends as DependsClass
from fastmcp import FastMCP
from fastmcp.server.context import Context
from pydantic import BaseModel

from app.core.database import async_session_factory

# MCP server 实例（§6.2：工具注册的真源，挂载到 FastAPI /mcp）
mcp = FastMCP("lanyuan")

# 已注册的 MCP 包装函数（工具名 → wrapped；测试/调试直接调用验证注入链）
_REGISTERED_TOOLS: dict[str, Callable] = {}


def _user_id_from_meta(ctx: Context) -> int:
    """从 callTool 请求的 `_meta` 提取 user_id（桥插件注入；Meta 是 pydantic 模型，extra=allow）"""
    meta = (ctx.request_context.meta if ctx.request_context is not None else None)
    uid = getattr(meta, "user_id", None) if meta is not None else None
    if uid is None:
        raise PermissionError("身份校验失败：桥层未注入 user_id（_meta）")
    return int(uid)


def _dep_name(default: Any) -> str:
    """提取 Depends(...) 的依赖函数名（同 v1 tool_registry._get_dep_name）"""
    if isinstance(default, DependsClass):
        dep = getattr(default, "dependency", None)
        return dep.__name__ if dep else ""
    return ""


def _classify(fn_sig: inspect.Signature, hints: dict) -> tuple[list[inspect.Parameter], tuple[str, type[BaseModel]] | None, str | None, str | None]:
    """签名 → (业务参数, Pydantic model 参数, db 注入参数名, user_id 注入参数名)

    注入识别与 v1 @tool 一致：Depends(get_db) → db 会话注入；
    Depends(get_current_user) → user_id 注入（MCP 模式来自 _meta）。
    Depends 参数一律不进 MCP schema（LLM 不可见）。
    Pydantic model 参数（如 PostCreate）单独返回——schema 展平为独立字段
    （同 v1 _flatten_model），执行时按字段重建 model 实例。
    """
    business: list[inspect.Parameter] = []
    model_param: tuple[str, type[BaseModel]] | None = None
    db_param = user_param = None
    for pname, param in fn_sig.parameters.items():
        default = param.default
        if default is not inspect.Parameter.empty and isinstance(default, DependsClass):
            dep = _dep_name(default)
            if dep == "get_db":
                db_param = pname
            elif dep == "get_current_user":
                user_param = pname
            continue
        hint = hints.get(pname, param.annotation)
        if isinstance(hint, type) and issubclass(hint, BaseModel):
            model_param = (pname, hint)
            continue
        business.append(param)
    return business, model_param, db_param, user_param


def _unwrap(result: Any) -> Any:
    """api_success 包装 → data（同 v1 ToolDef.execute 解包语义）"""
    if isinstance(result, dict) and "code" in result and "data" in result:
        return result["data"]
    return result


def _to_dict(result: Any) -> Any:
    """Pydantic/SQLAlchemy model → dict（result_formatter 契约：收到的一定是 dict/None/标量）

    同 v1 tool_registry._to_dict 逻辑：SQLAlchemy model 跳过 created_at/updated_at
    （onupdate 会触发异步懒加载 MissingGreenlet）。不 import v1 模块（§6.4b
    零 v1 依赖）——MCP server 独立于 v1 工具体系，仅内联等价逻辑。
    """
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "dict"):
        return result.dict()
    if hasattr(result, "_sa_instance_state") and hasattr(result, "__table__"):
        return {
            c.name: getattr(result, c.name)
            for c in result.__table__.columns
            if c.name not in ("created_at", "updated_at")
        }
    if isinstance(result, list):
        return [_to_dict(item) for item in result]
    return result


def mcp_tool(fn: Callable | None = None, *, name: str | None = None,
             result_formatter: Callable[[Any], str] | None = None) -> Callable:
    """@mcp_tool 装饰器：注册 MCP 业务工具（§6.4b），用法同 v1 @tool

    用法（v2 = v1-copy + support-mcp，2026-08-30 用户定——业务代码逐字复制
    v1，装饰器换 @mcp_tool）：
        @router.get("/user/me")
        @mcp_tool(result_formatter=_format_get_my_profile)
        async def get_my_profile(
            db: AsyncSession = Depends(get_db),
            user_id: int = Depends(get_current_user),
        ):
            ...
            return api_success(user)  # model 原样，formatter 删隐私

    签名中的 Depends(get_db)/Depends(get_current_user) 参数为注入参数
    （LLM 不可见）；其余参数进 schema。返回原函数（endpoint 无感）。

    result_formatter：同 v1 @tool 语义——MCP 模式下解包 api_success → _to_dict
    （model→dict）→ formatter 输出（JSON 字符串，LLM 读 formatter 投影）；
    无 formatter 时直接返回解包后的 data（结构化 dict）。
    """

    def _register(f: Callable) -> Callable:
        tool_name = name or f.__name__
        fn_sig = inspect.signature(f)
        hints = get_type_hints(f)

        business, model_param, db_param, user_param = _classify(fn_sig, hints)

        # ── 签名 → MCP schema（Depends 注入参数剔除；Pydantic model 展平；
        #    必填在前，组内保持函数顺序） ──
        sig_required: list[inspect.Parameter] = []
        sig_optional: list[inspect.Parameter] = []
        annotations: dict[str, Any] = {}
        for param in business:
            pname = param.name
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

        # Pydantic model 参数 → 字段展平为独立参数（同 v1 _flatten_model；
        # 字段重名跳过——v1 语义）
        model_fields_set: set[str] = set()
        if model_param is not None:
            _mname, model_cls = model_param
            for fname, finfo in model_cls.model_fields.items():
                if fname in annotations:
                    continue
                if finfo.is_required():
                    sig_required.append(inspect.Parameter(
                        fname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=finfo.annotation,
                    ))
                else:
                    default = finfo.get_default(call_default_factory=True)
                    sig_optional.append(inspect.Parameter(
                        fname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=finfo.annotation, default=default,
                    ))
                annotations[fname] = finfo.annotation
                model_fields_set.add(fname)

        sig_optional.append(inspect.Parameter(
            "ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context, default=None,
        ))
        annotations["ctx"] = Context

        business_names = [p.name for p in business]

        # ── 包装执行：_meta 身份 + db 会话（请求级 commit）→ 调 endpoint 函数 ──
        async def wrapped(ctx: Context = None, **kwargs: Any) -> Any:  # type: ignore[assignment]
            user_id = _user_id_from_meta(ctx)
            call_kwargs: dict[str, Any] = {}
            if model_param is not None:
                mname, mcls = model_param
                model_data = {k: v for k, v in kwargs.items() if k in model_fields_set}
                call_kwargs[mname] = mcls(**model_data)
            for pname in business_names:
                if pname in kwargs:
                    call_kwargs[pname] = kwargs[pname]
            async with async_session_factory() as db:
                try:
                    if user_param is not None:
                        call_kwargs[user_param] = user_id
                    if db_param is not None:
                        call_kwargs[db_param] = db
                    result = await f(**call_kwargs)
                    await db.commit()
                    result = _unwrap(result)
                    if result_formatter is not None:
                        return result_formatter(_to_dict(result))
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
        return f  # 返回原函数（endpoint 无感）

    return _register(fn) if fn is not None else _register

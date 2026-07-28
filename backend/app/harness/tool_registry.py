"""工具注册系统 — @tool 装饰器 + ToolRegistry

在 FastAPI router 函数上加 @tool，自动从函数签名 + 类型注解生成 LLM tool schema。
Pydantic model 参数（如 PostCreate、CommentCreate）自动展平为独立字段。
db 和 user_id 自动注入，不暴露给 LLM。
"""

import inspect
import json
import logging
import types as pytypes
from typing import Any, Callable, Optional, Union, get_args, get_origin, get_type_hints, Annotated

# Type alias for result formatter: (data) -> str
ResultFormatter = Callable[[Any], str]

from fastapi.params import Depends as DependsClass
from pydantic import BaseModel

from app.harness.hooks.events import emit_collect

logger = logging.getLogger(__name__)

# ── Python type → JSON Schema type mapping ──

_SIMPLE_TYPE_MAP: dict[type, dict] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
}


def _resolve_type(tp: Any) -> dict:
    """Python annotation → JSON Schema type fragment"""
    origin = get_origin(tp)
    args = get_args(tp) if origin else ()

    # Annotated[T, ...]: unwrap to the inner type
    if origin is Annotated:
        return _resolve_type(args[0])

    # Optional[X] / X | None: strip None, keep the base type
    if origin is Union or origin is pytypes.UnionType:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _resolve_type(non_none[0])
        if len(non_none) > 1:
            return {"oneOf": [_resolve_type(a) for a in non_none]}
        return {"type": "null"}

    if tp in _SIMPLE_TYPE_MAP:
        return _SIMPLE_TYPE_MAP[tp]

    if origin is list:
        item_tp = args[0] if args else str
        return {"type": "array", "items": _resolve_type(item_tp)}

    if origin is dict:
        return {"type": "object"}

    # Fallback
    return {"type": "string"}
def _get_dep_name(default: Any) -> str:
    """Extract the dependency function name from a DependsClass() default."""
    if isinstance(default, DependsClass):
        dep = getattr(default, "dependency", None)
        return dep.__name__ if dep else ""
    return ""


# ── ToolDef ──


class ToolDef:
    """单个工具定义：schema + 执行"""

    __slots__ = (
        "name", "description", "fn",
        "_pydantic_param", "_inject_db", "_inject_user",
        "result_formatter", "schema",
    )

    def __init__(self, name: str, description: str, fn: Callable,
                 result_formatter: Optional[ResultFormatter] = None):
        self.name = name
        self.description = description
        self.fn = fn
        self.result_formatter = result_formatter
        self._pydantic_param: Optional[str] = None
        self._inject_db = False
        self._inject_user = False
        self.schema = self._build_schema()

    # ── Schema 构建 ──

    def _build_schema(self) -> dict:
        sig = inspect.signature(self.fn)
        hints = get_type_hints(self.fn)

        properties: dict[str, Any] = {}
        required: list[str] = []

        for param_name, param in sig.parameters.items():
            default = param.default

            # --- DependsClass() params: skip from schema, mark for injection ---
            if default is not inspect.Parameter.empty and isinstance(default, DependsClass):
                dep_name = _get_dep_name(default)
                if dep_name == "get_db":
                    self._inject_db = True
                elif dep_name in ("get_current_user",):
                    self._inject_user = True
                continue

            hint = hints.get(param_name)

            # --- Pydantic model param → flatten its fields ---
            if hint is not None and isinstance(hint, type) and issubclass(hint, BaseModel):
                self._pydantic_param = param_name
                self._flatten_model(hint, properties, required)
                continue

            # --- Regular param ---
            schema = _resolve_type(hint) if hint else {"type": "string"}
            properties[param_name] = schema

            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    @staticmethod
    def _flatten_model(
        model_cls: type[BaseModel],
        properties: dict,
        required: list[str],
    ) -> None:
        """将 Pydantic model 的字段展平到 properties 中"""
        for field_name, field_info in model_cls.model_fields.items():
            if field_name in properties:
                continue
            field_schema = _resolve_type(field_info.annotation)
            if field_info.description:
                field_schema["description"] = field_info.description
            properties[field_name] = field_schema
            if field_info.is_required():
                required.append(field_name)

    # ── 执行 ──

    async def execute(self, db, user_id: int, args: dict) -> str:
        """执行工具，注入 db/user_id，传入 LLM 参数"""
        sig = inspect.signature(self.fn)
        hints = get_type_hints(self.fn)

        kwargs: dict[str, Any] = {}

        for param_name, param in sig.parameters.items():
            default = param.default

            # Inject db
            if default is not inspect.Parameter.empty and isinstance(default, DependsClass):
                dep_name = _get_dep_name(default)
                if dep_name == "get_db":
                    kwargs[param_name] = db
                elif dep_name in ("get_current_user",):
                    kwargs[param_name] = user_id
                continue

            # Reconstruct Pydantic model from flattened args
            hint = hints.get(param_name)
            if hint is not None and isinstance(hint, type) and issubclass(hint, BaseModel):
                model_fields = set(hint.model_fields.keys())
                model_data = {k: v for k, v in args.items() if k in model_fields}
                kwargs[param_name] = hint(**model_data)
                continue

            # Pass through from args
            if param_name in args:
                kwargs[param_name] = args[param_name]
            elif param.default is not inspect.Parameter.empty:
                kwargs[param_name] = param.default

        result = await self.fn(**kwargs)

        # Unwrap api_success wrapper
        if isinstance(result, dict) and "code" in result and "data" in result:
            result = result["data"]

        # 如果有 result_formatter，用它生成 LLM 友好的摘要文本
        if self.result_formatter:
            # Pydantic model → dict，formatter 统一处理 dict
            if hasattr(result, "model_dump"):
                result = result.model_dump()
            elif hasattr(result, "dict"):
                result = result.dict()
            return self.result_formatter(result)

        if isinstance(result, str):
            return result

        # SQLAlchemy model → dict（如 get_my_profile 返回的 User 对象）
        if hasattr(result, "_sa_instance_state") and hasattr(result, "__table__"):
            # 只读 LLM 关心的字段，跳过内部时间戳（updated_at 含 onupdate，会触发异步懒加载）
            result = {
                c.name: getattr(result, c.name)
                for c in result.__table__.columns
                if c.name not in ("created_at", "updated_at")
            }

        result = json.dumps(result, ensure_ascii=False, default=str)
        return result


# ── ToolRegistry ──


class ToolRegistry:
    """全局工具注册表"""

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(self, td: ToolDef) -> None:
        self._tools[td.name] = td
        logger.info("Tool registered: %s ← %s", td.name, td.fn.__module__)

    def get(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    def to_openai_tools(self) -> list[dict]:
        return [t.schema for t in self._tools.values()]

    @property
    def all(self) -> list[ToolDef]:
        return list(self._tools.values())

    async def execute(self, db, user_id: int, tool_call: dict) -> str:
        """兼容 OpenAI tool_call 格式的分发"""
        name = tool_call.get("function", {}).get("name", "")
        raw_args = tool_call.get("function", {}).get("arguments", "{}")

        tool = self._tools.get(name)
        if not tool:
            return f"未知工具: {name}"

        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            return f"参数解析失败: {raw_args}"

        # tool:pre — 可阻断
        blocked = await emit_collect("tool:pre", tool_name=name, args=args, raw_args=raw_args)
        if blocked:
            return str(blocked[0])

        result = await tool.execute(db, user_id, args)

        # tool:post — 清理、截断
        cleaned = await emit_collect("tool:post", tool_name=name, result=result)
        if cleaned:
            return str(cleaned[0])
        return result


# ── 全局实例 & @tool 装饰器 ──

registry = ToolRegistry()


def tool(fn=None, *, name: str = None, result_formatter: Optional[ResultFormatter] = None):
    """@tool 装饰器：将 router 函数注册为 AI tool

    用法：
        @tool
        async def list_posts(...): ...

        @tool(name="custom_name")
        async def my_func(...): ...

        @tool(result_formatter=my_formatter)
        async def list_posts(...): ...
          → 返回时自动调 my_formatter(data) 生成摘要文本
    """
    if fn is None:
        return lambda f: _register(f, name=name, result_formatter=result_formatter)
    return _register(fn, name=name, result_formatter=result_formatter)


def _register(fn: Callable, name: str = None,
              result_formatter: Optional[ResultFormatter] = None) -> Callable:
    tool_name = name or fn.__name__
    description = (fn.__doc__ or "").strip()
    td = ToolDef(tool_name, description, fn, result_formatter=result_formatter)
    registry.register(td)
    return fn  # 返回原函数，不影响 FastAPI router

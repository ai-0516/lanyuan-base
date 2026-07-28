"""
事件系统 — on() / emit() / emit_collect()

参考 Hermes Agent 的 HookRegistry 设计：
- emit():      即发即忘，不关心返回值（日志、监控）
- emit_collect(): 收集非 None 返回值（决策式：阻断、替换结果）

handler 异常只记日志，不阻断主流程。
"""

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

_handlers: dict[str, list[Callable]] = {}


def on(event: str):
    """装饰器：注册事件处理器

    用法：
        @on("tool:pre")
        async def log_tool_call(tool_name, args):
            logger.info("Tool: %s", tool_name)
    """
    def wrapper(fn: Callable):
        _handlers.setdefault(event, []).append(fn)
        return fn
    return wrapper


async def emit(event: str, **context) -> None:
    """触发事件，忽略 handler 返回值"""
    for fn in _handlers.get(event, []):
        try:
            result = fn(**context)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("事件处理器异常 [%s]", event)


async def emit_collect(event: str, **context) -> list[Any]:
    """触发事件，收集所有非 None 返回值

    用于 tool:pre（阻断）、tool:post（替换结果）等决策式场景。
    """
    results: list[Any] = []
    for fn in _handlers.get(event, []):
        try:
            result = fn(**context)
            if asyncio.iscoroutine(result):
                result = await result
            if result is not None:
                results.append(result)
        except Exception:
            logger.exception("事件处理器异常 [%s]", event)
    return results

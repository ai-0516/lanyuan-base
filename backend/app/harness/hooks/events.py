"""
事件系统 — on() / emit()

参考 Hermes Agent 的 HookRegistry 设计：
所有钩子都是辅助功能（日志、监控），即发即忘，不干涉主流程。

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
        @on("tool:start")
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

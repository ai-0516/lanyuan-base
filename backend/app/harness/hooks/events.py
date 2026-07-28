"""
事件系统 — 观察者模式

生产者通过 emit() 将事件放入 asyncio.Queue，后台 consumer Task
循环消费并调用 handler。emit() 是纯同步 put_nowait，不阻塞调用方。

handler 异常只记日志，不阻断 consumer 循环。
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

_handlers: dict[str, list[Callable[[dict], Any]]] = {}
_queue: asyncio.Queue["Event"] = asyncio.Queue()
_consumer_task: asyncio.Task | None = None


@dataclass
class Event:
    name: str
    data: dict[str, Any] = field(default_factory=dict)


def on(event: str):
    """装饰器：注册事件处理器

    用法：
        @on("tool:start")
        async def log_tool_call(data: dict):
            logger.info("Tool: %s", data["tool_name"])
    """
    def wrapper(fn: Callable):
        _handlers.setdefault(event, []).append(fn)
        return fn
    return wrapper


def emit(event: str, data: dict[str, Any] | None = None) -> None:
    """将事件放入队列，不阻塞调用方"""
    global _consumer_task
    if _consumer_task is None:
        _consumer_task = asyncio.create_task(_consumer())
    _queue.put_nowait(Event(event, data or {}))


def reset():
    """清空队列和 consumer（测试用）"""
    global _consumer_task
    if _consumer_task:
        _consumer_task.cancel()
        _consumer_task = None
    while not _queue.empty():
        _queue.get_nowait()


async def _consumer():
    """后台协程：循环消费队列事件"""
    while True:
        ev = await _queue.get()
        for fn in _handlers.get(ev.name, []):
            try:
                result = fn(ev.data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("事件处理器异常 [%s]", ev.name)

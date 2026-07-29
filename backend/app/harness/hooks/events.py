"""
事件系统 — 观察者模式

生产者通过 emit() 将事件放入 asyncio.Queue，后台 consumer Task
循环消费并调用 handler。emit() 是纯同步 put_nowait，不阻塞调用方。

handler 异常只记日志，不阻断 consumer 循环。
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, TypedDict, NotRequired

logger = logging.getLogger(__name__)


# ── 事件名常量 ──

AGENT_START = "agent:start"
AGENT_END = "agent:end"
TURN_START = "turn:start"
TURN_END = "turn:end"
LLM_START = "llm:start"
LLM_END = "llm:end"
LLM_ERROR = "llm:error"  # LLM 调用发生错误（API 错误、超时等）
TOOL_START = "tool:start"
TOOL_END = "tool:end"


# ── 事件数据类型 ──


class EventBase(TypedDict):
    """所有事件数据的基类"""
    req_id: str


class AgentStartData(EventBase):
    meta: dict


class TurnStartData(EventBase):
    turn: int


class TurnEndData(EventBase):
    turn: int


class LlmStartData(EventBase):
    turn: int
    messages_sent: list
    tools_sent: list | None


class LlmEndData(EventBase):
    turn: int
    finish_reason: str
    tokens: int
    content: str
    tool_calls: list
    tool_calls_count: int
    usage: NotRequired[dict[str, Any]]
    error: NotRequired[str]


class LlmErrorData(EventBase):
    """LLM 调用发生错误时的独立事件（API 拒绝、超时、解析失败等）"""
    turn: int
    error: str
    detail: NotRequired[str]


class ToolStartData(EventBase):
    tool_name: str
    tool_call_id: str


class ToolEndData(EventBase):
    tool_name: str
    tool_call_id: str
    result: str
    status: str  # "ok" | "error"


class AgentEndData(EventBase):
    total_turns: int
    error: str | None


# ── 实现 ──


_handlers: dict[str, list[Callable[[Mapping[str, Any]], Any]]] = {}
_queue: "asyncio.Queue[Event] | None" = None
_consumer_task: asyncio.Task | None = None


class Event:
    name: str
    data: Mapping[str, Any]

    def __init__(self, name: str, data: Mapping[str, Any] | None = None) -> None:
        self.name = name
        self.data = data or {}


def _ensure_queue() -> "asyncio.Queue[Event]":
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


def on(event: str):
    """装饰器：注册事件处理器

    用法：
        @on(events.TOOL_END)
        async def log_tool_call(data: events.ToolEndData):
            logger.info("Tool: %s", data["tool_name"])
    """
    def wrapper(fn: Callable):
        _handlers.setdefault(event, []).append(fn)
        return fn
    return wrapper


def emit(event: str, data: Mapping[str, Any] | None = None) -> None:
    """将事件放入队列，不阻塞调用方"""
    global _consumer_task
    if _consumer_task is None:
        _consumer_task = asyncio.create_task(_consumer())
    _ensure_queue().put_nowait(Event(event, data or {}))


def reset():
    """清空队列和 consumer（测试用）"""
    global _consumer_task, _queue
    if _consumer_task:
        _consumer_task.cancel()
        _consumer_task = None
    q = _queue
    _queue = None
    if q:
        while not q.empty():
            q.get_nowait()


async def _consumer():
    """后台协程：循环消费队列事件"""
    q = _ensure_queue()
    while True:
        ev = await q.get()
        for fn in _handlers.get(ev.name, []):
            try:
                result = fn(ev.data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("事件处理器异常 [%s]", ev.name)

"""v2 事件层（薄）：白名单过滤 + SSE 帧（TECH_SPEC §4，2026-08-23 用户定）

定位：初期唯一职责 = 白名单过滤——只把前端关心的 event 发过去；
事件格式保留 DSH 原样（type + data 不改写）。翻译/改写是扩展点（管道式
filter → [改写] → frame），初期改写环节为空。

白名单（§4.2）：assistant/chunk(仅 text-delta) / step/start / user/message /
turn/start / turn/end；其余（tool/*、reasoning-delta、session.status 等）
后端消费或丢弃。
"""

from __future__ import annotations

import json
from collections.abc import Callable

# 白名单：type → 子类型过滤条件（None = 全透传）
# assistant/chunk 只放行 text-delta（thinking/用量/块边界不转发）
_WHITELIST: dict[str, Callable[[dict], bool] | None] = {
    "assistant/chunk": lambda data: (data.get("chunk") or {}).get("type") == "text-delta",
    "step/start": None,
    "user/message": None,
    "turn/start": None,
    "turn/end": None,
}


def should_forward(event: dict) -> bool:
    """白名单过滤：是否发给前端（§4.2）"""
    etype = event.get("type")
    if etype not in _WHITELIST:
        return False
    cond = _WHITELIST[etype]
    return True if cond is None else cond(event.get("data") or {})


def format_sse(event: dict) -> str:
    """SSE 帧：`event: <type>\\ndata: <json>\\n\\n`（type + data 原样透传）"""
    payload = {"type": event.get("type"), "data": event.get("data") or {}}
    return f"event: {event.get('type')}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def is_done_event(event: dict) -> bool:
    """done 判定：turn/end（§4.3；session.status=idle 兜底由调用方处理）"""
    return event.get("type") == "turn/end"

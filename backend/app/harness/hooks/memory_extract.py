"""
记忆抽取钩子 — agent:end 异步提取

每轮 agent 结束后，后台 consumer 异步调用 provider.extract()，
把用户对话中值得跨会话记住的信息写入记忆库。

设计：
- 在 agent:start 时记录 (req_id → user_id/session_id)，agent:end 时取出使用
  （agent:end 事件本身只带 total_turns/error，上下文需从 start 时暂存）
- 只抽取当前会话的消息（按 conversation_id 过滤），避免跨用户串味
- 提取失败只记日志，不影响主流程（hook 是纯辅助）
"""

import logging

from sqlalchemy import select

from app.core.database import async_session_factory
from app.harness.hooks import events
from app.harness.hooks.events import on
from app.harness.memory import get_provider
from app.models.conversation import Message

logger = logging.getLogger("app.harness.hooks.memory_extract")

# req_id → {"user_id": int, "session_id": int}，agent:start 记录、agent:end 消费
_ctx: dict[str, dict] = {}


@on(events.AGENT_START)
async def on_agent_start(data: dict):
    req_id = data.get("req_id", "-")
    meta = data.get("meta", {}) or {}
    if req_id != "-":
        _ctx[req_id] = {
            "user_id": meta.get("user_id"),
            "session_id": meta.get("session_id"),
        }


@on(events.AGENT_END)
async def on_agent_end(data: dict):
    req_id = data.get("req_id", "-")
    ctx = _ctx.pop(req_id, None)
    if not ctx:
        return
    user_id = ctx.get("user_id")
    session_id = ctx.get("session_id")
    if user_id is None or session_id is None:
        return

    try:
        async with async_session_factory() as db:
            # 读取当前会话最近消息（作为抽取输入）
            stmt = (
                select(Message)
                .where(Message.conversation_id == session_id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(20)
            )
            result = await db.execute(stmt)
            rows: list[Message] = list(result.scalars().all())
            rows.reverse()  # 正序

            messages = []
            for m in rows:
                if m.role == "user" and isinstance(m.content, str) and m.content.strip():
                    messages.append({"role": "user", "content": m.content})

            provider = get_provider()
            added = await provider.extract(db, user_id, messages)
            if added:
                logger.info("记忆抽取完成: user_id=%s session=%s 新增=%s 条",
                            user_id, session_id, added)
            await db.commit()
    except Exception:
        logger.exception("记忆抽取异常: user_id=%s session=%s", user_id, session_id)

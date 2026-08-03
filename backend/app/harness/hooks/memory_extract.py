"""
记忆抽取钩子 — agent:end 异步提取

每轮 agent 结束后，后台 consumer 异步调用 memory.extract()，
把用户对话中值得跨会话记住的信息写入记忆库。

设计：
- AGENT_END 事件直接携带 meta（user_id/session_id，agent.py 统一 emit，
  review #4），hook 无需自行暂存身份
- 只抽取当前会话的消息（按 conversation_id 过滤），避免跨用户串味
- 提取失败只记日志，不影响主流程（hook 是纯辅助）
"""

import logging

from sqlalchemy import select

from app.core.database import async_session_factory
from app.harness import memory
from app.harness.hooks import events
from app.harness.hooks.events import on
from app.models.conversation import Message

logger = logging.getLogger("app.harness.hooks.memory_extract")


@on(events.AGENT_END)
async def on_agent_end(data: dict):
    meta = data.get("meta", {}) or {}
    user_id = meta.get("user_id")
    session_id = meta.get("session_id")
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

            added = await memory.extract(db, user_id, messages)
            if added:
                logger.info("记忆抽取完成: user_id=%s session=%s 新增=%s 条",
                            user_id, session_id, added)
            await db.commit()
    except Exception:
        logger.exception("记忆抽取异常: user_id=%s session=%s", user_id, session_id)

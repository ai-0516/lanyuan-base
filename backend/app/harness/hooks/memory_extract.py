"""
记忆抽取钩子 — agent:end 异步提取

每轮 agent 结束后，后台 consumer 异步调用 memory.extract()，
把这一轮实际发送给 LLM 的消息（含用户消息/工具结果）中值得记住的信息写入记忆库。

设计（review #1/#4）：
- 不用 session_id 查 DB（消息可能尚未落库，且非本轮消息会混入）——
  改为监听 LLM_START，暂存该轮发送给 LLM 的完整 messages 快照（req_id 关联），
  AGENT_END 时按 req_id 取出消费，保证抽取输入就是「这一轮调用的全部消息」
- 身份（user_id）由 AGENT_END 事件 meta 直接携带，hook 不自行暂存
- 快照在 AGENT_END 时 pop（AGENT_END 由 agent.py try/finally 保证必然 emit，
  不会泄漏）
- 提取失败只记日志，不影响主流程（hook 是纯辅助）
"""

import logging

from app.core.database import async_session_factory
from app.harness import memory
from app.harness.hooks import events
from app.harness.hooks.events import on

logger = logging.getLogger("app.harness.hooks.memory_extract")

# req_id → 该轮发送给 LLM 的完整消息快照（LLM_START 暂存、AGENT_END 消费）
_messages: dict[str, list] = {}


@on(events.LLM_START)
async def on_llm_start(data: dict):
    req_id = data.get("req_id", "-")
    sent = data.get("messages_sent")
    if req_id != "-" and isinstance(sent, list) and sent:
        # 每轮覆盖：最后一次 LLM_START 的 messages_sent 即本轮完整消息
        _messages[req_id] = sent


@on(events.AGENT_END)
async def on_agent_end(data: dict):
    req_id = data.get("req_id", "-")
    messages = _messages.pop(req_id, None)
    if not messages:
        return

    meta = data.get("meta", {}) or {}
    user_id = meta.get("user_id")
    session_id = meta.get("session_id")
    if user_id is None:
        return

    try:
        async with async_session_factory() as db:
            added = await memory.extract(db, user_id, messages)
            if added:
                logger.info("记忆抽取完成: user_id=%s session=%s 新增=%s 条",
                            user_id, session_id, added)
            await db.commit()
    except Exception:
        logger.exception("记忆抽取异常: user_id=%s session=%s", user_id, session_id)

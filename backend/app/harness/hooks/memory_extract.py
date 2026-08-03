"""
跨会话记忆抽取钩子 — session:end 触发

session 结束（前端 /new 开启新对话）时，后台 consumer 异步调用 memory.extract()，
把该 session 的完整对话中值得记住的信息写入记忆库。

设计（2026-08-03 粒度设计，替代原每轮 agent:end 抽取）：
- **粒度 = session**：抽取只在 session 边界发生，不在每轮对话后做。
  理由：① session 内多轮对话的连续性由消息历史天然保证，记忆只解决
  「跨 session」的部分；② 每轮抽取会导致记忆频繁变化 → system 字节变化
  → 前缀缓存全断；③ 每轮抽取碎片化（只看到单轮消息），session 结束
  用完整对话抽取质量更高、频率更低
- 事件驱动：ai_service /new 处 emit SESSION_END（携带 user_id/session_id），
  本 hook 消费。事件直接携带身份，hook 无需自行暂存
- 从 DB 读该 session 全部消息（session 结束时消息均已落库，不需要
  LLM_START 快照——上一版 #1 的快照机制随之移除）
- 抽取失败只记日志，不影响主流程（hook 是纯辅助）
"""

import logging

from app.core.database import async_session_factory
from app.harness import context, memory
from app.harness.hooks import events
from app.harness.hooks.events import on

logger = logging.getLogger("app.harness.hooks.memory_extract")


@on(events.SESSION_END)
async def on_session_end(data: dict):
    user_id = data.get("user_id")
    session_id = data.get("session_id")
    if user_id is None or session_id is None:
        return

    try:
        async with async_session_factory() as db:
            # 读该 session 全部消息（时间正序），转为 OpenAI 格式供 extract
            history = await context.get_recent_messages(db, session_id)
            messages = [
                {"role": m.role, "content": m.content}
                for m in history
                if m.content
            ]
            if not messages:
                return

            added = await memory.extract(db, user_id, messages)
            if added:
                logger.info("记忆抽取完成: user_id=%s session=%s 新增=%s 条",
                            user_id, session_id, added)
            await db.commit()
    except Exception:
        logger.exception("记忆抽取异常: user_id=%s session=%s", user_id, session_id)

"""AI 对话 Manager

职责：
- 编排 AIAgent 的完整调用流程（数据准备 → 执行 → 持久化）
- AIAgent 只负责跟 LLM 交互，不接触 DB
"""

import logging
import secrets

from app.harness import context, memory, session
from app.harness.agent import AIAgent
from app.harness.hooks import events
from app.harness.tools import TOOLS, execute_tool
from app.schemas.ai import MessageItem, SessionResponse

logger = logging.getLogger(__name__)


async def get_or_create_session(db, user_id: int) -> SessionResponse:
    """获取会话（后端决定新建或复用）"""
    conv = await session.get_or_create(db, user_id)
    recent = await context.get_recent_messages(db, conv.id)

    return SessionResponse(
        session_id=conv.id,
        title=conv.title or "",
        messages=[
            MessageItem(
                id=m.id, role=m.role,
                content=m.content, created_at=m.created_at,
            )
            for m in recent
        ],
    )


async def stream_chat(db, user_id: int, session_id: int, message: str):
    """发送消息，SSE 流式返回"""

    # ── 0. 处理 /new 命令 ──
    if message.strip() == "/new":
        # session 结束（2026-08-03 粒度设计）：emit session:end，hook 异步抽取
        # 该 session 的完整对话为跨会话记忆。事件驱动，不阻塞 /new 响应。
        logger.info("发起 session:end: user=%s session=%s，触发跨会话记忆抽取",
                    user_id, session_id)
        events.emit(events.SESSION_END, {
            "req_id": secrets.token_hex(4),
            "user_id": user_id,
            "session_id": session_id,
        })
        new_conv = await session.create_new(db, user_id)
        await db.commit()
        yield ("cmd_new_session", new_conv.id)
        yield ("done", "")
        return

    # ── 1. 归属校验 ──
    conv = await session.verify_ownership(db, session_id, user_id)
    if conv is None:
        yield ("error", "会话不存在或无权限访问")
        return

    # ── 2. 保存用户消息 ──
    await session.save_user_message(db, session_id, message)

    # ── 3. 构建上下文 ──
    history = await context.get_recent_messages(db, session_id)
    # 跨会话记忆（2026-08-03 粒度设计）：只注入记忆索引（全部记忆 description），
    # 不注入相关记忆（select_relevant 是动态选择，每轮结果可能不同，是缓存杀手）。
    # 索引是静态的：记忆只在 session 结束（/new）时抽取，session 内不变 →
    # 每轮 build_memory_index 字节相同 → system 前缀缓存命中。
    memory_index = await memory.build_memory_index(db, user_id)
    deepseek_messages = context.build_deepseek_messages(
        history,
        message,
        memory_index=memory_index,
    )

    # ── 4. Agent Loop（含工具调用） ──
    agent = AIAgent(tools=TOOLS, tool_executor=execute_tool)
    logger.info("Agent 启动: session_id=%s user_id=%s tools=%d", session_id, user_id, len(TOOLS))
    try:
        async for event, data in agent.run(
            deepseek_messages,
            db=db,
            user_id=user_id,
            meta={"session_id": session_id, "user_id": user_id, "user_message": message},
        ):
            # LLM 返回的错误事件（非异常），记录 ERROR 级别便于 error.log 定位
            if event == "error":
                logger.error("Agent 返回错误: session_id=%s user_id=%s error=%s",
                             session_id, user_id, data)
            yield (event, data)
    except Exception:
        logger.exception("stream_chat 异常: session_id=%s user_id=%s", session_id, user_id)
        error_reply = "抱歉，AI 回复被中断，请重试。"
        await session.save_assistant_message(db, session_id, error_reply)
        await session.touch_conversation(db, session_id)
        yield ("error", error_reply)
        return

    # ── 5. 持久化：从 agent log 回填真实 tool_call 结构 ──
    try:
        log = agent.get_log()
        if log.get("turns"):
            for turn in log["turns"]:
                tc = turn.get("tool_calls", [])
                if tc:
                    # 保存 assistant tool_call 消息（含真实 tool_calls 结构）
                    await session.save_tool_call_message(
                        db, session_id, tc,
                        content=turn.get("content") or None,
                    )
                    # 保存 tool 执行结果
                    for tr in turn.get("tool_results", []):
                        await session.save_tool_result_message(
                            db, session_id,
                            tool_call_id=tr.get("tool_call_id", ""),
                            content=tr.get("result", ""),
                        )
                else:
                    # 纯文本回复
                    if turn.get("content"):
                        await session.save_assistant_message(
                            db, session_id, turn["content"],
                        )
            await session.touch_conversation(db, session_id)

    except Exception:
        logger.exception("持久化/log 异常: session_id=%s user_id=%s", session_id, user_id)
        # 持久化失败不影响已发出的 SSE 事件，仅记录日志

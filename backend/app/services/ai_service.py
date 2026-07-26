"""AI 对话 Manager

职责：
- 编排 AIAgent 的完整调用流程（数据准备 → 执行 → 持久化）
- AIAgent 只负责跟 LLM 交互，不接触 DB
"""

from app.harness import context, session
from app.harness.agent import AIAgent, _write_log_entry
from app.harness.tools import TOOLS, execute_tool
from app.schemas.ai import MessageItem, SessionResponse


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
    deepseek_messages = context.build_deepseek_messages(history, message)

    # ── 4. Agent Loop（含工具调用） ──
    agent = AIAgent(tools=TOOLS, tool_executor=execute_tool)
    try:
        async for event, data in agent.run(
            deepseek_messages,
            db=db,
            user_id=user_id,
            meta={"session_id": session_id, "user_message": message},
        ):
            yield (event, data)
    except Exception as e:
        error_reply = f"抱歉，AI 回复被中断，请重试。错误：{str(e)}"
        await session.save_assistant_message(db, session_id, error_reply)
        await session.touch_conversation(db, session_id)
        yield ("error", error_reply)
        return

    # ── 5. 持久化：从 agent log 回填真实 tool_call 结构 ──
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

    # ── 6. 写 LLM 请求日志（完整轮次，一条记录） ──
    _write_log_entry(agent.get_log())

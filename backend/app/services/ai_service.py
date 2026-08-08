"""AI 对话 Manager

职责：
- 编排 AIAgent 的完整调用流程（数据准备 → 执行 → 持久化）
- AIAgent 只负责跟 LLM 交互，不接触 DB
- 压缩旋转（rotation）：上下文超限时自动结束旧会话、创建新会话（TECH_SPEC 8.3）
"""

import json
import logging
import secrets

from sqlalchemy import select

from app.config import settings
from app.harness import context, context_compact, memory, session
from app.harness.agent import AIAgent
from app.harness.hooks import events
from app.harness.tools import TOOLS, execute_tool
from app.models.llm_usage import LlmUsage
from app.schemas.ai import MessageItem, SessionResponse

logger = logging.getLogger(__name__)


async def _get_last_total_tokens(db, session_id: int) -> int | None:
    """该会话最近一次 LLM 调用的精确 token 数（llm_usage 表）

    超限判断依据（PR #49 review：不用字符估算，LLM response 带精确 usage）。
    用 total_tokens（= prompt + completion）：本轮生成的 assistant 回复
    会作为下轮 prompt 的一部分，total 比 prompt 更贴近「会话内容总量」。
    返回 None = 会话尚无任何 LLM 调用（新会话首条消息），不可能超限。
    """
    result = await db.execute(
        select(LlmUsage.total_tokens)
        .where(LlmUsage.session_id == session_id)
        .order_by(LlmUsage.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _maybe_rotate(db, user_id: int, session_id: int, u_k_id: int) -> int | None:
    """超限检测 + 压缩旋转（TECH_SPEC 8.3）

    返回新 session_id（已旋转）或 None（未超限 / 摘要失败不旋转）。
    调用时机：u_k 已保存到当前会话之后、构建上下文之前。

    旋转动作：
    - 摘要输入 = 当前会话除 u_k 外全部消息（llm 层压缩，现有逻辑不变）
    - 创建 B，u_k 从 A 迁移（tail = 1 条，触发压缩的那条消息）
    - assistant(tool_call: compress_context) + tool(摘要) 写入 B（role 交替合法）
    - emit SESSION_END(A)（u_k 已迁移，A 消息集 = 被压缩部分）
    - system_prompt 缓存 pop(A)（死数据清理，TECH_SPEC 8.7）
    """
    history = await context.get_recent_messages(db, session_id)
    messages = context.orm_to_canonical(history)

    # 超限判断：用该会话最近一次 LLM 调用的精确 total_tokens（llm_usage 表），
    # 不用字符估算（PR #49 review：LLM response 自带精确 usage 信息）
    last_total_tokens = await _get_last_total_tokens(db, session_id)
    if last_total_tokens is None:
        return None  # 新会话首条消息（尚无 LLM 调用），不可能超限
    if last_total_tokens < settings.SESSION_ROTATION_THRESHOLD:
        return None

    # 摘要输入排除 u_k（它是 B 的起点，原样保留在新会话，避免重复总结）
    summary_input = messages[:-1]
    if not summary_input:
        return None

    try:
        summary = await context_compact._summarize(summary_input)
    except context_compact.LLMSummaryError:
        logger.exception("rotation 摘要失败: user=%s session=%s，不旋转", user_id, session_id)
        return None

    new_conv = await session.create_new(db, user_id)
    await session.move_message(db, u_k_id, new_conv.id)

    tool_call_id = f"compress_{secrets.token_hex(8)}"
    await session.save_tool_call_message(
        db,
        new_conv.id,
        [{
            "id": tool_call_id,
            "type": "function",
            "function": {"name": "compress_context",
                         "arguments": json.dumps({"trigger": "context overflow"}, ensure_ascii=False)},
        }],
        content=None,
    )
    await session.save_tool_result_message(
        db, new_conv.id, tool_call_id=tool_call_id, content=summary
    )

    events.emit(events.SESSION_END, {
        "req_id": secrets.token_hex(4),
        "user_id": user_id,
        "session_id": session_id,
    })
    context.invalidate_session_prompt(session_id)
    await db.commit()

    summary_input_chars = len(json.dumps(summary_input, ensure_ascii=False, default=str))
    logger.info(
        "rotation: user=%s A=%s → B=%s 摘要输入=%d字符(%d条) → 摘要=%d字符",
        user_id, session_id, new_conv.id,
        summary_input_chars, len(summary_input), len(summary),
    )
    return new_conv.id


async def get_or_create_session(db, user_id: int) -> SessionResponse:
    """获取会话（后端决定新建或复用）"""
    conv = await session.get_or_create(db, user_id)
    recent = await context.get_recent_messages(db, conv.id)

    return SessionResponse(
        session_id=conv.id,
        title=conv.title or "",
        messages=[
            MessageItem(
                id=m.id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
            )
            for m in recent
        ],
    )


async def stream_chat(db, user_id: int, session_id: int, message: str):
    """发送消息，SSE 流式返回"""

    # ── 1. 归属校验（前端传入的 session_id 只做归属校验，TECH_SPEC 8.2）──
    conv = await session.verify_ownership(db, session_id, user_id)
    if conv is None:
        yield ("error", "会话不存在或无权限访问")
        return

    # ── 1.5 实际会话 = 用户最新一条（rotation 后前端 session_id 可能已过期，
    #      消息必须写入最新会话——前端无感，TECH_SPEC 8.1/8.3）──
    active = await session.get_or_create(db, user_id)
    session_id = active.id

    # ── 2. 保存用户消息 ──
    msg = await session.save_user_message(db, session_id, message)

    # ── 2.5 压缩旋转（TECH_SPEC 8.3）：超限 → 摘要 + 建新会话 + u_k 迁移 ──
    new_id = await _maybe_rotate(db, user_id, session_id, msg.id)
    if new_id is not None:
        session_id = new_id

    # ── 3. 构建上下文 ──
    history = await context.get_recent_messages(db, session_id)
    # 跨会话记忆（2026-08-03 粒度设计 + 2026-08-05 session 冻结）：
    # 只注入记忆索引（全部记忆 description），不注入相关记忆（动态选择是缓存杀手）。
    # 索引在 session 首次组装 system prompt 时注入后冻结（缓存 key=session_id），
    # 之后即使 LLM 主动 memory_add 使索引变化也不重组装——新记忆下个 session 生效，
    # 保持 system 字节稳定 → 前缀缓存命中，token 成本优先。
    memory_index = await memory.build_memory_index(db, user_id)
    canonical_messages = context.build_messages(
        history,
        message,
        memory_index=memory_index,
        session_id=str(session_id),
    )

    # ── 4. Agent Loop（含工具调用） ──
    agent = AIAgent(tools=TOOLS, tool_executor=execute_tool)
    logger.info("Agent 启动: session_id=%s user_id=%s tools=%d", session_id, user_id, len(TOOLS))
    try:
        async for event, data in agent.run(
            canonical_messages,
            db=db,
            user_id=user_id,
            meta={"session_id": session_id, "user_id": user_id, "user_message": message},
        ):
            # LLM 返回的错误事件（非异常），记录 ERROR 级别便于 error.log 定位
            if event == "error":
                logger.error("Agent 返回错误: session_id=%s user_id=%s error=%s", session_id, user_id, data)
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
                        db,
                        session_id,
                        tc,
                        content=turn.get("content") or None,
                    )
                    # 保存 tool 执行结果
                    for tr in turn.get("tool_results", []):
                        await session.save_tool_result_message(
                            db,
                            session_id,
                            tool_call_id=tr.get("tool_call_id", ""),
                            content=tr.get("result", ""),
                            tool_name=tr.get("tool"),
                        )
                else:
                    # 纯文本回复
                    if turn.get("content"):
                        await session.save_assistant_message(
                            db,
                            session_id,
                            turn["content"],
                        )
            await session.touch_conversation(db, session_id)

    except Exception:
        logger.exception("持久化/log 异常: session_id=%s user_id=%s", session_id, user_id)
        # 持久化失败不影响已发出的 SSE 事件，仅记录日志

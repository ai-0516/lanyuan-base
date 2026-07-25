"""AIAgent — 对话 Agent

瞬态实例，用完即弃。不缓存任何会话状态，所有数据通过 harness 模块读写 MySQL。

职责：
- init_session: 获取/创建会话
- run: Agent Loop（发消息 → LLM → 判断是否调工具 → 继续或结束）

每个 AIAgent 对应一个 user_id（不一定绑定 session_id，因为 init_session 会设置它）。
"""

from app.config import settings
from app.harness import context, session, streaming
from app.schemas.ai import MessageItem, SessionResponse


class AIAgent:
    """AI 对话 Agent

    用法：
        agent = AIAgent(user_id=1)
        result = await agent.init_session(db)       # POST /session
        async for ev, data in agent.run(db, "你好"): # POST /chat
            ...
    """

    def __init__(self, user_id: int, session_id: int | None = None):
        self.user_id = user_id
        self.session_id = session_id

    # ── 会话 ───────────────────────────────────────

    async def init_session(self, db) -> SessionResponse:
        """获取/创建会话，返回 session_id + 历史消息"""
        conv = await session.get_or_create(db, self.user_id)
        self.session_id = conv.id

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

    # ── Agent Loop ─────────────────────────────────

    async def run(self, db, message: str):
        """Agent Loop

        1. 保存用户消息
        2. 读取历史上下文
        3. 调 LLM（模拟或 DeepSeek API）
        4. 保存 AI 回复
        5. 刷新会话时间

        产出 (event, data) 元组，符合 SSE 事件协议：
          ("token", content)  — AI 回复文字
          ("done", "")        — 流结束
          ("error", msg)      — 错误提示

        注：当前无工具调用，Loop 是直线（LLM → 返回）。
            后续添加 tool_use 时在此处插判断分支。
        """
        if self.session_id is None:
            yield ("error", "session_id 未设置，请先调用 init_session")
            return

        # ── 1. 归属校验 ──
        conv = await session.verify_ownership(db, self.session_id, self.user_id)
        if conv is None:
            yield ("error", "会话不存在或无权限访问")
            return

        # ── 2. 保存用户消息 ──
        await session.save_user_message(db, self.session_id, message)

        # ── 3. 构建上下文 ──
        history = await context.get_recent_messages(db, self.session_id)
        deepseek_messages = context.build_deepseek_messages(history, message)

        # ── 4. 选择数据源 ──
        source = streaming.deepseek_chat if settings.DEEPSEEK_API_KEY else streaming.mock_chat

        # ── 5. Agent Loop — 当前只有直线路径 ──
        full_reply = ""
        try:
            async for event, data in source(deepseek_messages):
                if event == "token":
                    full_reply += data
                yield (event, data)
        except Exception as e:
            error_reply = f"抱歉，AI 回复被中断，请重试。错误：{str(e)}"
            await session.save_assistant_message(db, self.session_id, error_reply)
            await session.touch_conversation(db, self.session_id)
            yield ("error", error_reply)
            return

        # ── 6. 持久化 ──
        if full_reply:
            await session.save_assistant_message(db, self.session_id, full_reply)
            await session.touch_conversation(db, self.session_id)

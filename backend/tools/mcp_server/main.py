"""lanyuan 业务工具 MCP server（TECH_SPEC §6）：fastmcp，stdio transport

由 DSH runtime 侧桥插件（@lanyuan/dsh-lanyuan-bridge）spawn，每 worker 常驻一个。
工具注册名经桥插件映射为 mcp__lanyuan__<rawName>（§6.1）。

身份设计（§6.3）：工具签名**不含** user_id 参数（LLM 不可见）；执行时身份来自
桥插件在 callTool 请求 `_meta` 中注入的 user_id（桥层强制绑定，LLM 无法伪造）。
任何工具实现不得信任模型输入中的身份字段——本文件统一从 `_meta` 提取。

首批工具（§6.4）：search_history / get_profile，复用 v1 @tool 的执行逻辑
（backend/app/api/v1/ai.py / profile.py）；FTS5 投影（§8.3）依赖 M3 persistence，
M2 沿用 v1 LIKE 实现。

启动：.venv/bin/python backend/tools/mcp_server/main.py（cwd=backend/，桥插件指定）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # backend/

from fastmcp import FastMCP  # noqa: E402
from fastmcp.server.context import Context  # noqa: E402
from sqlalchemy import func, or_, select  # noqa: E402

from app.core.database import async_session_factory  # noqa: E402
from app.models.conversation import Conversation, Message  # noqa: E402
from app.models.user import User  # noqa: E402

mcp = FastMCP("lanyuan")

# ── 身份提取（§6.3：唯一来源 = 桥插件注入的 _meta，拒绝模型输入中的身份字段） ──

_META_ERR = "身份校验失败：桥层未注入 user_id（_meta）"


def _user_id(ctx: Context) -> int:
    """从 callTool 请求的 `_meta` 提取 user_id（桥插件注入；Meta 是 pydantic 模型，extra=allow）"""
    meta = (ctx.request_context.meta if ctx.request_context is not None else None)
    uid = getattr(meta, "user_id", None) if meta is not None else None
    if uid is None:
        raise PermissionError(_META_ERR)
    return int(uid)


# ── search_history：搜索用户过往对话历史（复用 v1 ai.py 逻辑，PR #51） ──

_MAX_SNIPPET = 4000  # 窗口内单条消息截断长度（防 payload 爆炸，对齐 Hermes）


def _truncate(content: str | None, limit: int = _MAX_SNIPPET) -> str:
    """截断消息内容到 limit 字符"""
    if not content:
        return ""
    return content[:limit] + "…" if len(content) > limit else content


def _merge_overlapping_hits(hits: list[Message], window: int) -> list[list[Message]]:
    """把窗口重叠的命中合并为连续片段（PR #51 review）

    为什么需要合并：每条命中会带 ±window 条的上下文窗口（context_window）。
    同一会话内相邻的两条命中（消息 id 差 ≤ 2×window）的窗口必然重叠——
    如果各自独立成 result，同一批消息会重复出现在多个 result 里（浪费
    token，且 LLM 看到的内容互相矛盾）。合并后一个片段对应一个 result，
    context_window 取整个区间，天然去重。

    段序如何保持：片段按「段内首条 hit 在 hits 中的原始下标」升序排列，
    保证输出顺序与用户请求的 sort（newest/oldest/relevance）一致。
    """
    by_conv: dict[int, list[tuple[int, Message]]] = {}
    for idx, hit in enumerate(hits):
        by_conv.setdefault(hit.conversation_id, []).append((idx, hit))

    segments: list[tuple[int, list[Message]]] = []
    for conv_hits in by_conv.values():
        conv_hits.sort(key=lambda t: t[1].id)
        for idx, hit in conv_hits:
            can_merge = bool(
                segments
                and segments[-1][1][-1].conversation_id == hit.conversation_id
                and hit.id - segments[-1][1][-1].id <= 2 * window
            )
            if can_merge:
                segments[-1][1].append(hit)
            else:
                segments.append((idx, [hit]))

    return [seg for _, seg in sorted(segments, key=lambda t: t[0])]


@mcp.tool()
async def search_history(
    query: str,
    limit: int = 3,
    window: int = 5,
    sort: str = "relevance",
    ctx: Context = None,  # type: ignore[assignment]  # fastmcp 注入，不进 schema
) -> dict:
    """搜索用户过往对话历史。当用户提到过去聊过的内容、或需要回忆更早
    对话细节时使用。返回命中的消息及其上下文窗口（最多 limit 条命中，
    每条带前后 window 条上下文）。

    语义说明：返回的 total = **合并后片段数**（segment 数）——同一会话内
    窗口重叠的连续命中合并为一个片段（同会话连续命中只返回一条窗口；
    跨会话命中各自独立），因此 total 可能小于实际命中条数。
    """
    user_id = _user_id(ctx)
    # 关键词拆词：空格分隔，任一命中即返回（OR，PR #51 review——AND 容易什么都搜不到）
    keywords = [kw for kw in query.strip().split() if kw]
    if not keywords:
        return {"results": [], "total": 0}

    def _escape_like(s: str) -> str:
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    async with async_session_factory() as db:
        # 当前活跃会话（用户最新）——其内容 agent 上下文已有，排除减少噪音
        latest = (
            await db.execute(
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                .limit(1)
            )
        ).scalars().first()
        current_conv_id = latest.id if latest else None

        stmt = (
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Conversation.user_id == user_id,
                Message.role.in_(["user", "assistant"]),
                Message.content.isnot(None),
            )
        )
        if keywords:
            stmt = stmt.where(or_(*[
                Message.content.like(f"%{_escape_like(kw)}%", escape="\\")
                for kw in keywords
            ]))
        if current_conv_id is not None:
            stmt = stmt.where(Message.conversation_id != current_conv_id)
        stmt = stmt.order_by(Message.id.asc() if sort == "oldest" else Message.id.desc()).limit(limit)
        hits = (await db.execute(stmt)).scalars().all()

        segments = _merge_overlapping_hits(hits, window)

        results = []
        for seg in segments:
            seg_min, seg_max = seg[0].id - window, seg[-1].id + window
            anchor = seg[0]

            context_msgs = (
                await db.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == anchor.conversation_id,
                        Message.id >= seg_min,
                        Message.id <= seg_max,
                    )
                    .order_by(Message.id.asc())
                )
            ).scalars().all()

            before_total = await db.scalar(
                select(func.count()).select_from(Message).where(
                    Message.conversation_id == anchor.conversation_id, Message.id < seg_min
                )
            )
            after_total = await db.scalar(
                select(func.count()).select_from(Message).where(
                    Message.conversation_id == anchor.conversation_id, Message.id > seg_max
                )
            )

            results.append({
                "message_id": anchor.id,
                "role": anchor.role,
                "content": _truncate(anchor.content),
                "created_at": anchor.created_at.isoformat() if anchor.created_at else "",
                "conversation_id": anchor.conversation_id,
                "messages_before": before_total or 0,
                "messages_after": after_total or 0,
                "context_window": [
                    {
                        "message_id": m.id,
                        "role": m.role,
                        "content": _truncate(m.content),
                        "created_at": m.created_at.isoformat() if m.created_at else "",
                    }
                    for m in context_msgs
                ],
            })

        return {"results": results, "total": len(results)}


# ── get_profile：当前用户资料（复用 v1 profile.py + formatter 删减原则） ──

def _strip_private(data: dict) -> dict:
    """删减：avatar（base64 头像）、openid/unionid（微信身份标识）、unit/room（房号隐私）。
    其余字段（昵称/小区/楼栋/简介/开关）原样保留。"""
    return {k: v for k, v in data.items() if k not in {"avatar", "openid", "unionid", "unit", "room"}}


@mcp.tool()
async def get_profile(ctx: Context = None) -> dict:  # type: ignore[assignment]
    """获取当前用户的基本资料（昵称、小区、楼栋、简介等；房号/头像为隐私不返回）。"""
    user_id = _user_id(ctx)
    async with async_session_factory() as db:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise ValueError(f"用户不存在: {user_id}")
    return _strip_private({
        "id": user.id,
        "nickname": user.nickname,
        "community": user.community,
        "building": user.building,
        "bio": user.bio,
        "show_building": user.show_building,
        "show_room": user.show_room,
    })


if __name__ == "__main__":
    mcp.run()  # stdio transport

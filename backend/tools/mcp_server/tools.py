"""MCP 业务工具（TECH_SPEC §6.4b：@mcp_tool 原生注册，不依赖 v1 @tool）

工具即注册（装饰器执行时进 mcp）；身份 `user_id`/会话 `db` 为注入参数
（LLM 不可见，由 @mcp_tool 从 _meta / async_session_factory 注入）。

注意：v1 的 search_history 不迁移——v2 历史搜索由 DSH session-query 能力
覆盖（§6.4b）；此处只保留业务工具。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from tools.mcp_server.decorator import mcp_tool


@mcp_tool
async def get_my_profile(user_id: int | None = None, db: AsyncSession | None = None) -> dict:
    """获取当前用户的基本资料（昵称、小区、楼栋、简介等；房号/头像为隐私不返回）。"""
    assert user_id is not None and db is not None  # 注入参数：@mcp_tool 从 _meta/会话注入
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise ValueError(f"用户不存在: {user_id}")
    return {
        "id": user.id,
        "nickname": user.nickname,
        "community": user.community,
        "building": user.building,
        "bio": user.bio,
        "show_building": user.show_building,
        "show_room": user.show_room,
    }

"""v2 用户 API（TECH_SPEC §6.4b：工具=endpoint，@mcp_tool 直接写在 endpoint 上）

与 v1 @tool 同模式：endpoint 函数双形态——HTTP 消费统一响应格式
（api_success），MCP 消费由 @mcp_tool 解包 data（LLM 看到结构化 dict）。
注入参数（db/user_id）用 Depends 声明，@mcp_tool 按 dep 名识别（LLM 不可见）；
MCP 模式下 user_id 来自 callTool `_meta`（§6.3），db 由装饰器注入会话。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_success
from app.models.user import User
from tools.mcp_server.decorator import mcp_tool

router = APIRouter(prefix="/user", tags=["用户 v2"])


@router.get("/me")
@mcp_tool
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取当前用户的基本资料（昵称、小区、楼栋、简介等；房号/头像为隐私不返回）。

    隐私字段（avatar/openid/unionid/unit/room）由工具自身控制不返回。
    """
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        # 查询无果是正常查询结果（code=0 + data=null），不是业务失败
        return api_success(None)
    return api_success({
        "id": user.id,
        "nickname": user.nickname,
        "community": user.community,
        "building": user.building,
        "bio": user.bio,
        "show_building": user.show_building,
        "show_room": user.show_room,
    })

"""v2 用户 API（TECH_SPEC §6.4b：工具=endpoint，@mcp_tool 直接写在 endpoint 上）

**v2 = v1-copy + support-mcp（2026-08-30 用户定）**：业务代码逐字复制 v1
（endpoint + Depends 注入 + api_success + result_formatter），装饰器从 @tool
换成 @mcp_tool——HTTP 模式 FastAPI Depends 正常解析；MCP 模式 @mcp_tool
注入 user_id（_meta）/db（会话）→ 解包 api_success → _to_dict → result_formatter
输出（LLM 读 formatter 投影，同 v1 ToolDef.execute 语义）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_success
from app.harness.tool_registry import dumps, strip_keys
from app.models.user import User
from tools.mcp_server.decorator import mcp_tool

router = APIRouter(prefix="/user", tags=["用户 v2"])


def _format_get_my_profile(data) -> str:
    """删减：avatar（base64 头像）、openid/unionid（微信身份标识）、unit/room（房号隐私）。
    其余字段（昵称/小区/楼栋/简介/开关）原样保留。"""
    return dumps(strip_keys(data, {"avatar", "openid", "unionid", "unit", "room"}))


@router.get("/me")
@mcp_tool(result_formatter=_format_get_my_profile)
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取当前登录用户的个人信息。"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    return api_success(user)

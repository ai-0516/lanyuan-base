"""通知相关 API"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_success
from app.harness.tool_registry import dumps, strip_keys, tool
from tools.mcp_server.decorator import mcp_tool
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["通知"])


def _format_list_notifications(data) -> str:
    """删减：通知发起者 from_user.avatar（base64 头像，LLM 不需要）。其余结构原样保留。"""
    return dumps(strip_keys(data, {"avatar"}))


def _format_notification_count(data) -> str:
    """无删减：返回原始结果（{count}）"""
    return dumps(data)


def _format_mark_all_read(data) -> str:
    """无删减：返回原始结果（{updated} = 已读条数）"""
    return dumps(data)


@router.get("")
@mcp_tool(result_formatter=_format_list_notifications)
@tool(result_formatter=_format_list_notifications)
async def list_notifications(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取当前用户的所有未读通知，返回通知列表。"""
    notifications = await notification_service.get_unread_notifications(db, user_id)
    return api_success(notifications)


@router.get("/count")
@mcp_tool(name="notification_count", result_formatter=_format_notification_count)
@tool(name="notification_count", result_formatter=_format_notification_count)
async def notification_count(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取当前用户的未读通知数量。"""
    count = await notification_service.get_unread_count(db, user_id)
    return api_success({"count": count})


@router.put("/read-all")
@mcp_tool(result_formatter=_format_mark_all_read)
@tool(result_formatter=_format_mark_all_read)
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """将所有未读通知标记为已读。"""
    updated = await notification_service.mark_all_as_read(db, user_id)
    return api_success({"updated": updated})


@router.post("/read")
async def mark_read(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """标记为已读"""
    post_id = data.get("postId", 0)
    updated = await notification_service.mark_as_read(db, user_id, post_id)
    return api_success({"updated": updated})

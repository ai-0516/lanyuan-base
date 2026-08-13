"""通知相关 API"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_success
from app.harness.tool_registry import tool
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["通知"])


def _format_notifications(data) -> str:
    """通知列表 → LLM 摘要（不含 base64 头像）"""
    if not data:
        return "暂无未读通知"
    lines = [f"共 {len(data)} 条未读通知："]
    for n in data:
        from_user = n.get("from_user", {})
        lines.append(
            f"  {n.get('type', '?')}｜{from_user.get('nickname', '?')} → {n.get('post_title', '')}"
        )
    return "\n".join(lines)


def _format_notification_count(data) -> str:
    """未读数量 → LLM 摘要"""
    return f"未读通知 {data.get('count', 0)} 条"


def _format_mark_all_read(data) -> str:
    """标记已读结果 → LLM 摘要"""
    return f"已将 {data.get('updated', 0)} 条通知标记为已读"


@router.get("")
@tool(result_formatter=_format_notifications)
async def list_notifications(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取当前用户的所有未读通知，返回通知列表。"""
    notifications = await notification_service.get_unread_notifications(db, user_id)
    return api_success(notifications)


@router.get("/count")
@tool(name="notification_count", result_formatter=_format_notification_count)
async def notification_count(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取当前用户的未读通知数量。"""
    count = await notification_service.get_unread_count(db, user_id)
    return api_success({"count": count})


@router.put("/read-all")
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

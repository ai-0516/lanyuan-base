"""通知相关 API"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_success
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["通知"])


@router.get("")
async def list_notifications(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """未读通知列表"""
    notifications = await notification_service.get_unread_notifications(db, user_id)
    return api_success(notifications)


@router.get("/count")
async def notification_count(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """未读通知数量"""
    count = await notification_service.get_unread_count(db, user_id)
    return api_success({"count": count})


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

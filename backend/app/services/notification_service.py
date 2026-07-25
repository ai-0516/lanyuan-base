"""通知业务逻辑"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.post import Post
from app.models.user import User
from app.schemas.common import UserBrief
from app.schemas.notification import NotificationCount, NotificationResponse


async def get_unread_notifications(db: AsyncSession, user_id: int) -> list[NotificationResponse]:
    """获取用户的所有未读通知，按时间倒序"""
    stmt = (
        select(Notification, User, Post)
        .join(User, Notification.from_user_id == User.id)
        .join(Post, Notification.post_id == Post.id)
        .where(Notification.user_id == user_id, Notification.is_read == False)
        .order_by(Notification.created_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    notifications = []
    for notif, from_user, post in rows:
        post_title = post.content[:30] + "..." if len(post.content) > 30 else post.content
        notifications.append(
            NotificationResponse(
                id=notif.id,
                type=notif.type.value if hasattr(notif.type, 'value') else notif.type,
                from_user=UserBrief(
                    id=from_user.id,
                    nickname=from_user.nickname,
                    avatar=from_user.avatar,
                ),
                post_id=notif.post_id,
                post_title=post_title,
                comment_id=notif.comment_id,
                is_read=notif.is_read,
                created_at=notif.created_at,
            )
        )
    return notifications


async def get_unread_count(db: AsyncSession, user_id: int) -> int:
    """获取未读通知数量"""
    stmt = select(func.count(Notification.id)).where(
        Notification.user_id == user_id,
        Notification.is_read == False,
    )
    result = await db.execute(stmt)
    return result.scalar() or 0


async def mark_all_as_read(db: AsyncSession, user_id: int) -> int:
    """将所有未读通知标记为已读，返回更新的行数"""
    stmt = (
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.is_read == False,
        )
        .values(is_read=True, read_at=func.now())
    )
    result = await db.execute(stmt)
    return result.rowcount


async def mark_as_read(db: AsyncSession, user_id: int, post_id: int) -> int:
    """将某一帖子相关的所有通知标记为已读，返回更新的行数"""
    stmt = (
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.post_id == post_id,
            Notification.is_read == False,
        )
        .values(is_read=True, read_at=func.now())
    )
    result = await db.execute(stmt)
    return result.rowcount

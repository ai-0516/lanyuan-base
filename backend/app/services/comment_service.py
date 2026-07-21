"""评论业务逻辑"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import Comment
from app.models.notification import Notification
from app.models.post import Post
from app.models.user import User
from app.schemas.comment import CommentCreate, CommentResponse
from app.schemas.common import ReplyTo, UserBrief


async def create_comment(
    db: AsyncSession,
    user_id: int,
    post_id: int,
    data: CommentCreate,
) -> CommentResponse:
    """创建评论"""
    comment = Comment(
        post_id=post_id,
        user_id=user_id,
        parent_comment_id=data.parent_comment_id,
        content=data.content,
    )
    db.add(comment)
    await db.flush()
    await db.refresh(comment)

    # 获取评论者信息
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one()

    # 构建 reply_to
    reply_to = None
    notif_type = "comment"
    if data.parent_comment_id:
        parent_result = await db.execute(
            select(Comment, User)
            .join(User, Comment.user_id == User.id)
            .where(Comment.id == data.parent_comment_id)
        )
        parent_row = parent_result.one_or_none()
        if parent_row:
            parent_comment, parent_user = parent_row
            reply_to = ReplyTo(
                user_id=parent_user.id,
                nickname=parent_user.nickname,
            )
            notif_type = "reply"

    # 发送通知
    post_result = await db.execute(select(Post).where(Post.id == post_id))
    post = post_result.scalar_one_or_none()
    # 回复评论时通知被回复者，否则通知帖主
    target_user_id = parent_user.id if notif_type == "reply" and parent_row else post.user_id
    if target_user_id != user_id:
        notif = Notification(
            user_id=target_user_id,
            type=notif_type,
            from_user_id=user_id,
            post_id=post_id,
            comment_id=comment.id,
        )
        db.add(notif)

    await db.flush()

    return CommentResponse(
        id=comment.id,
        user=UserBrief(id=user.id, nickname=user.nickname, avatar=user.avatar),
        content=comment.content,
        reply_to=reply_to,
        created_at=comment.created_at,
    )


async def delete_comment(db: AsyncSession, comment_id: int, user_id: int) -> bool:
    """删除评论（仅评论作者或帖主可以）"""
    result = await db.execute(
        select(Comment).where(Comment.id == comment_id)
    )
    comment = result.scalar_one_or_none()
    if not comment:
        return False

    # 校验权限：评论作者本人 或 帖主
    if comment.user_id != user_id:
        post_result = await db.execute(select(Post).where(Post.id == comment.post_id))
        post = post_result.scalar_one_or_none()
        if not post or post.user_id != user_id:
            return False

    await db.delete(comment)
    return True


async def get_post_comments(db: AsyncSession, post_id: int) -> list[CommentResponse]:
    """获取帖子的所有评论"""
    stmt = (
        select(Comment)
        .where(Comment.post_id == post_id)
        .order_by(Comment.created_at.asc())
    )
    result = await db.execute(stmt)
    comments = result.scalars().all()

    items = []
    for cm in comments:
        user_result = await db.execute(select(User).where(User.id == cm.user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            continue

        reply_to = None
        if cm.parent_comment_id:
            parent_result = await db.execute(
                select(Comment, User)
                .join(User, Comment.user_id == User.id)
                .where(Comment.id == cm.parent_comment_id)
            )
            parent_row = parent_result.one_or_none()
            if parent_row:
                parent_comment, parent_user = parent_row
                reply_to = ReplyTo(
                    user_id=parent_user.id,
                    nickname=parent_user.nickname,
                )

        items.append(
            CommentResponse(
                id=cm.id,
                user=UserBrief(id=user.id, nickname=user.nickname, avatar=user.avatar),
                content=cm.content,
                reply_to=reply_to,
                created_at=cm.created_at,
            )
        )

    return items

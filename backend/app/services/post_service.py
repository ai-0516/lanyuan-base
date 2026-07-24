"""帖子业务逻辑"""

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.post import Post
from app.models.comment import Comment
from app.models.like import Like
from app.models.notification import Notification
from app.models.user import User
from app.schemas.post import CommentItem, PostCreate, PostListResponse, PostResponse
from app.schemas.common import ReplyTo, UserBrief


async def create_post(db: AsyncSession, user_id: int, data: PostCreate) -> PostResponse:
    """创建帖子并返回完整信息"""
    post = Post(
        user_id=user_id,
        content=data.content,
        images=data.images or [],
    )
    db.add(post)
    await db.flush()

    # 获取作者信息
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()

    return PostResponse(
        id=post.id,
        user=UserBrief(id=user.id, nickname=user.nickname, avatar=user.avatar),
        content=post.content,
        images=post.images if isinstance(post.images, list) else [],
        liked=False,
        comments=[],
        created_at=datetime.utcnow(),
    )


async def get_posts(
    db: AsyncSession,
    current_user_id: int,
    page: int = 1,
    size: int = 20,
) -> PostListResponse:
    """获取帖子列表（时间倒序，含评论和点赞）"""
    offset = (page - 1) * size

    # 查总数
    count_stmt = select(func.count(Post.id))
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    # 查帖子
    stmt = (
        select(Post)
        .order_by(Post.created_at.desc())
        .offset(offset)
        .limit(size)
    )
    result = await db.execute(stmt)
    posts = result.scalars().all()

    items = []
    for post in posts:
        # 作者信息
        user_result = await db.execute(select(User).where(User.id == post.user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            continue

        # 当前用户是否点赞
        liked_stmt = select(Like).where(
            Like.post_id == post.id, Like.user_id == current_user_id
        )
        liked_result = await db.execute(liked_stmt)
        liked = liked_result.scalar_one_or_none() is not None

        # 全部评论（不折叠）
        comment_stmt = (
            select(Comment)
            .where(Comment.post_id == post.id)
            .order_by(Comment.created_at.asc())
        )
        comment_result = await db.execute(comment_stmt)
        comments_raw = comment_result.scalars().all()

        comments = []
        for cm in comments_raw:
            # 评论者信息
            cu_result = await db.execute(select(User).where(User.id == cm.user_id))
            cu = cu_result.scalar_one_or_none()
            if not cu:
                continue

            reply_to = None
            if cm.parent_comment_id:
                parent_result = await db.execute(
                    select(Comment, User).join(User, Comment.user_id == User.id).where(
                        Comment.id == cm.parent_comment_id
                    )
                )
                parent_row = parent_result.one_or_none()
                if parent_row:
                    parent_comment, parent_user = parent_row
                    reply_to = ReplyTo(
                        user_id=parent_user.id,
                        nickname=parent_user.nickname,
                    )

            comments.append(
                CommentItem(
                    id=cm.id,
                    user=UserBrief(id=cu.id, nickname=cu.nickname, avatar=cu.avatar),
                    content=cm.content,
                    reply_to=reply_to,
                    created_at=cm.created_at,
                )
            )

        # 点赞者名单（最多 5 人）
        likers_stmt = (
            select(User)
            .join(Like, Like.user_id == User.id)
            .where(Like.post_id == post.id)
        )
        likers_result = await db.execute(likers_stmt)
        likers = [
            UserBrief(id=lu.id, nickname=lu.nickname, avatar=lu.avatar)
            for lu in likers_result.scalars().all()
        ]

        items.append(
            PostResponse(
                id=post.id,
                user=UserBrief(id=user.id, nickname=user.nickname, avatar=user.avatar),
                content=post.content,
                images=post.images if isinstance(post.images, list) else [],
                liked=liked,
                comments=comments,
                likers=likers,
                created_at=post.created_at,
            )
        )

    return PostListResponse(items=items, total=total, page=page, size=size)


async def delete_post(db: AsyncSession, post_id: int, user_id: int) -> bool:
    """删除帖子（仅作者）"""
    result = await db.execute(
        select(Post).where(Post.id == post_id, Post.user_id == user_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        return False

    # 级联删除
    await db.execute(delete(Comment).where(Comment.post_id == post_id))
    await db.execute(delete(Like).where(Like.post_id == post_id))
    await db.execute(delete(Notification).where(Notification.post_id == post_id))
    await db.delete(post)
    return True


async def toggle_like(
    db: AsyncSession, post_id: int, user_id: int
) -> tuple[bool, int]:
    """切换点赞状态，返回 (是否已点赞, 点赞总数)"""
    result = await db.execute(
        select(Like).where(Like.post_id == post_id, Like.user_id == user_id)
    )
    existing = result.scalar_one_or_none()

    if existing:
        await db.delete(existing)
        liked = False
    else:
        like = Like(post_id=post_id, user_id=user_id)
        db.add(like)

        # 给帖子作者发通知
        post_result = await db.execute(select(Post).where(Post.id == post_id))
        post = post_result.scalar_one_or_none()
        if post and post.user_id != user_id:
            notif = Notification(
                user_id=post.user_id,
                type="like",
                from_user_id=user_id,
                post_id=post_id,
            )
            db.add(notif)

        liked = True

    await db.flush()

    count_result = await db.execute(
        select(func.count(Like.id)).where(Like.post_id == post_id)
    )
    like_count = count_result.scalar() or 0

    return liked, like_count

"""帖子相关 API"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_error, api_success
from app.schemas.post import PostCreate
from app.services import post_service

router = APIRouter(prefix="/posts", tags=["帖子"])


@router.get("")
async def list_posts(
    page: int = 1,
    size: int = 20,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """帖子列表（时间倒序，含评论和点赞）"""
    result = await post_service.get_posts(db, user_id, page, size)
    return api_success(result)


@router.post("")
async def create_post(
    data: PostCreate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """发布帖子"""
    result = await post_service.create_post(db, user_id, data)
    return api_success(result)


@router.get("/{post_id}")
async def get_post(
    post_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取单个帖子详情"""
    result = await post_service.get_post_by_id(db, post_id, user_id)
    if not result:
        return api_error(40401, "帖子不存在")
    return api_success(result)


@router.delete("/{post_id}")
async def delete_post(
    post_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """删除帖子（仅作者）"""
    success = await post_service.delete_post(db, post_id, user_id)
    if not success:
        api_error(40301, "无权删除此帖子")
    return api_success({})


@router.post("/{post_id}/like")
async def toggle_like(
    post_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """点赞/取消点赞"""
    liked, like_count = await post_service.toggle_like(db, post_id, user_id)
    return api_success({"liked": liked, "likeCount": like_count})

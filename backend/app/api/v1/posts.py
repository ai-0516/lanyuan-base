"""帖子相关 API"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.schemas.post import PostCreate, PostListResponse, PostResponse
from app.schemas.common import SuccessResponse
from app.services import post_service

router = APIRouter(prefix="/posts", tags=["帖子"])


@router.get("", response_model=PostListResponse)
async def list_posts(
    page: int = 1,
    size: int = 20,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """帖子列表（时间倒序，含评论和点赞）"""
    return await post_service.get_posts(db, user_id, page, size)


@router.post("", response_model=PostResponse)
async def create_post(
    data: PostCreate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """发布帖子"""
    return await post_service.create_post(db, user_id, data)


@router.delete("/{post_id}", response_model=SuccessResponse)
async def delete_post(
    post_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """删除帖子（仅作者）"""
    success = await post_service.delete_post(db, post_id, user_id)
    if not success:
        raise HTTPException(status_code=403, detail="无权删除此帖子")
    return SuccessResponse()


@router.post("/{post_id}/like")
async def toggle_like(
    post_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """点赞/取消点赞"""
    liked, like_count = await post_service.toggle_like(db, post_id, user_id)
    return {"liked": liked, "likeCount": like_count}

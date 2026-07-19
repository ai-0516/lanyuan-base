"""评论相关 API"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.schemas.comment import CommentCreate, CommentResponse
from app.schemas.common import SuccessResponse
from app.services import comment_service

router = APIRouter(tags=["评论"])


@router.get("/posts/{post_id}/comments")
async def list_comments(
    post_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取帖子评论（已包含在帖子列表中，此接口用于单独获取）"""
    return await comment_service.get_post_comments(db, post_id)


@router.post("/posts/{post_id}/comments", response_model=CommentResponse)
async def create_comment(
    post_id: int,
    data: CommentCreate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """添加评论"""
    return await comment_service.create_comment(db, user_id, post_id, data)


@router.delete("/comments/{comment_id}", response_model=SuccessResponse)
async def delete_comment(
    comment_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """删除评论（仅作者或帖主）"""
    success = await comment_service.delete_comment(db, comment_id, user_id)
    if not success:
        raise HTTPException(status_code=403, detail="无权删除此评论")
    return SuccessResponse()

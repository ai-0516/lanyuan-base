"""评论相关 API"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_error, api_success
from app.harness.tool_registry import dumps, strip_keys, tool
from app.schemas.comment import CommentCreate
from app.services import comment_service

router = APIRouter(tags=["评论"])


def _format_list_comments(data) -> str:
    """删减：每条评论的 user.avatar（base64 头像，LLM 不需要）。其余结构原样保留。"""
    return dumps(strip_keys(data, {"avatar"}))


def _format_create_comment(data) -> str:
    """删减：评论者 user.avatar（base64 头像，LLM 不需要）。返回创建结果 JSON。"""
    return dumps(strip_keys(data, {"avatar"}))


def _format_delete_comment(data) -> str:
    """无删减：返回原始结果（{} = 已删除，null = 无权/不存在）"""
    return dumps(data)


@router.get("/posts/{post_id}/comments")
@tool(result_formatter=_format_list_comments)
async def list_comments(
    post_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取某个帖子的全部评论，按时间正序排列。"""
    result = await comment_service.get_post_comments(db, post_id)
    return api_success(result)


@router.post("/posts/{post_id}/comments")
@tool(result_formatter=_format_create_comment)
async def create_comment(
    post_id: int,
    data: CommentCreate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """对帖子添加评论。支持回复他人评论（传入 parent_comment_id 表示回复某人）。"""
    result = await comment_service.create_comment(db, user_id, post_id, data)
    return api_success(result)


@router.delete("/comments/{comment_id}")
@tool(result_formatter=_format_delete_comment)
async def delete_comment(
    comment_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """删除自己的评论（仅评论作者或帖主可以操作）"""
    success = await comment_service.delete_comment(db, comment_id, user_id)
    if not success:
        return api_error(40302, "无权删除此评论")
    return api_success({})

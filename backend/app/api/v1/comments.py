"""评论相关 API"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_error, api_success
from app.harness.tool_registry import tool
from app.schemas.comment import CommentCreate
from app.services import comment_service

router = APIRouter(tags=["评论"])


def _format_comments(data) -> str:
    """评论列表 → LLM 摘要（不含 base64 头像）"""
    if not data:
        return "暂无评论"
    lines = [f"共 {len(data)} 条评论："]
    for c in data:
        user = c.get("user", {})
        reply = ""
        if c.get("reply_to"):
            reply = f"（回复 {c['reply_to'].get('nickname', '?')}）"
        lines.append(f"  {user.get('nickname', '?')}{reply}：{c.get('content', '')}")
    return "\n".join(lines)


def _format_comment(data) -> str:
    """单条评论（创建结果）→ LLM 摘要"""
    if not data:
        return "评论操作失败"
    user = data.get("user", {})
    return f"评论已发布｜{user.get('nickname', '?')}：{data.get('content', '')}"


def _format_delete_comment(data) -> str:
    """删除评论结果 → LLM 摘要"""
    if data is None:
        return "删除失败（无权删除或评论不存在）"
    return "评论已删除"


@router.get("/posts/{post_id}/comments")
@tool(result_formatter=_format_comments)
async def list_comments(
    post_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取某个帖子的全部评论，按时间正序排列。"""
    result = await comment_service.get_post_comments(db, post_id)
    return api_success(result)


@router.post("/posts/{post_id}/comments")
@tool(result_formatter=_format_comment)
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

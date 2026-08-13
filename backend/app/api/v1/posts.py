"""帖子相关 API"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_error, api_success
from app.harness.tool_registry import dumps, strip_keys, tool
from app.schemas.post import PostCreate
from app.services import post_service

router = APIRouter(prefix="/posts", tags=["帖子"])


def _format_list_posts(data: dict) -> str:
    """删减：帖子作者/评论者/点赞者 avatar（base64 头像，LLM 不需要）。其余结构原样保留。"""
    return dumps(strip_keys(data, {"avatar"}))


def _format_create_post(data) -> str:
    """删减：作者 avatar（base64 头像，LLM 不需要）。返回创建结果 JSON。"""
    return dumps(strip_keys(data, {"avatar"}))


def _format_get_post(data) -> str:
    """删减：作者/评论者/点赞者 avatar（base64 头像，LLM 不需要）。帖子详情结构原样保留。"""
    return dumps(strip_keys(data, {"avatar"}))


def _format_delete_post(data) -> str:
    """无删减：返回原始结果（{} = 已删除，null = 无权/不存在）"""
    return dumps(data)


def _format_like_post(data) -> str:
    """无删减：返回原始结果（{liked, likeCount}，null = 帖子不存在）"""
    return dumps(data)


def _format_unlike_post(data) -> str:
    """无删减：返回原始结果（{unliked, likeCount}，null = 帖子不存在）"""
    return dumps(data)


@router.get("")
@tool(result_formatter=_format_list_posts)
async def list_posts(
    page: int = 1,
    size: int = 20,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取社区帖子列表，按时间倒序。返回每条帖子的内容、作者信息、评论列表和点赞详情。"""
    result = await post_service.get_posts(db, user_id, page, size)
    return api_success(result)


@router.post("")
@tool(result_formatter=_format_create_post)
async def create_post(
    data: PostCreate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """发布帖子到社区。用户在这里分享生活、寻求帮助或组织活动。支持图文混排，最多9张图片。"""
    result = await post_service.create_post(db, user_id, data)
    return api_success(result)


@router.get("/{post_id}")
@tool(result_formatter=_format_get_post)
async def get_post(
    post_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取单个帖子的详细信息，包括全部评论和点赞者名单。"""
    result = await post_service.get_post_by_id(db, post_id, user_id)
    if not result:
        # 查无此帖是正常查询结果（code=0 + data=null），非业务失败（issue #19 同 get_user_public）
        return api_success(None)
    return api_success(result)


@router.delete("/{post_id}")
@tool(result_formatter=_format_delete_post)
async def delete_post(
    post_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """删除自己的帖子（仅作者可以操作）"""
    success = await post_service.delete_post(db, post_id, user_id)
    if not success:
        return api_error(40301, "无权删除此帖子")
    return api_success({})


@router.post("/{post_id}/like")
@tool(result_formatter=_format_like_post)
async def like_post(
    post_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """点赞帖子。如果已经点过赞则无操作，不会重复点赞。"""
    liked, like_count = await post_service.like_post(db, post_id, user_id)
    if liked is None:
        return api_error(40401, "帖子不存在")
    return api_success({"liked": liked, "likeCount": like_count})


@router.delete("/{post_id}/like")
@tool(result_formatter=_format_unlike_post)
async def unlike_post(
    post_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """取消点赞帖子。如果未点赞则无操作。"""
    unliked, like_count = await post_service.unlike_post(db, post_id, user_id)
    return api_success({"unliked": unliked, "likeCount": like_count})

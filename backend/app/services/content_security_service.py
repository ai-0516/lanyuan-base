"""公开内容安全校验。"""

import logging
from urllib.parse import unquote, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.wechat import wechat_client
from app.models.post import MediaModerationTask, Post
from app.models.user import User

logger = logging.getLogger(__name__)


class UnsafeContentError(Exception):
    """微信判定内容需要拦截。"""


class ContentSecurityUnavailableError(Exception):
    """内容安全服务暂时不可用。"""


async def check_public_text(db: AsyncSession, user_id: int, content: str, scene: int) -> None:
    """校验公开发布文本；仅微信明确返回 pass 时放行。"""
    result = await db.execute(select(User.openid).where(User.id == user_id))
    openid = result.scalar_one_or_none()
    if not openid:
        raise ContentSecurityUnavailableError("用户 openid 不存在")

    try:
        suggestion = await wechat_client.msg_sec_check(content, openid, scene)
    except Exception as exc:
        logger.warning(
            "Content security check unavailable for user_id=%s scene=%s",
            user_id,
            scene,
            exc_info=True,
        )
        raise ContentSecurityUnavailableError from exc

    if suggestion != "pass":
        raise UnsafeContentError


def _validate_image_pair(file_id: str, media_url: str) -> None:
    """确保送检 URL 来自云存储且路径与待发布 fileID 一致。"""
    if not file_id.startswith("cloud://"):
        raise ContentSecurityUnavailableError("图片 fileID 非法")
    parsed = urlparse(media_url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".tcb.qcloud.la"):
        raise ContentSecurityUnavailableError("图片临时 URL 非法")
    cloud_path = file_id.split("/", 3)[-1]
    if not unquote(parsed.path).endswith("/" + cloud_path):
        raise ContentSecurityUnavailableError("图片 URL 与 fileID 不匹配")


async def submit_post_images(
    db: AsyncSession,
    user_id: int,
    post_id: int,
    file_ids: list[str],
    media_urls: list[str],
) -> bool:
    """为帖子图片创建异步检测任务；本地 mock 直接通过。"""
    if len(file_ids) != len(media_urls) or not file_ids:
        raise ContentSecurityUnavailableError("图片检测参数不完整")
    result = await db.execute(select(User.openid).where(User.id == user_id))
    openid = result.scalar_one_or_none()
    if not openid:
        raise ContentSecurityUnavailableError("用户 openid 不存在")

    traces: list[tuple[str, str]] = []
    try:
        for file_id, media_url in zip(file_ids, media_urls, strict=True):
            _validate_image_pair(file_id, media_url)
            trace_id = await wechat_client.media_check_async(media_url, openid, scene=3)
            if trace_id:
                traces.append((trace_id, file_id))
    except Exception as exc:
        logger.warning(
            "Media security submission unavailable for user_id=%s post_id=%s",
            user_id,
            post_id,
            exc_info=True,
        )
        raise ContentSecurityUnavailableError from exc

    db_post = await db.get(Post, post_id)
    if not traces:
        db_post.moderation_status = "approved"
        return True
    for trace_id, file_id in traces:
        db.add(MediaModerationTask(post_id=post_id, trace_id=trace_id, file_id=file_id))
    return False


async def apply_media_result(
    db: AsyncSession, trace_id: str, suggestion: str, errcode: int
) -> bool:
    """幂等应用微信图片检测回调；所有图片通过后才公开帖子。"""
    result = await db.execute(
        select(MediaModerationTask).where(MediaModerationTask.trace_id == trace_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        return False
    task.status = "passed" if errcode == 0 and suggestion == "pass" else "rejected"
    post = await db.get(Post, task.post_id)
    if not post:
        return True
    if task.status == "rejected":
        post.moderation_status = "rejected"
        return True
    statuses_result = await db.execute(
        select(MediaModerationTask.status).where(MediaModerationTask.post_id == post.id)
    )
    statuses = list(statuses_result.scalars())
    if statuses and all(status == "passed" for status in statuses):
        post.moderation_status = "approved"
    return True

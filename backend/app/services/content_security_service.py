"""公开内容安全校验。"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.wechat import wechat_client
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

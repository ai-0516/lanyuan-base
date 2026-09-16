"""公开内容安全校验。"""

import logging
from urllib.parse import unquote, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.wechat import WeChatSecurityScene, wechat_client
from app.core.moderation import (
    MediaModerationResourceType,
    MediaModerationTaskStatus,
    PostModerationStatus,
)
from app.models.post import MediaModerationTask, Post
from app.models.parking_rental import ParkingRental
from app.models.user import User

logger = logging.getLogger(__name__)


class UnsafeContentError(Exception):
    """微信判定内容需要拦截。"""


class ContentSecurityUnavailableError(Exception):
    """内容安全服务暂时不可用。"""


class InvalidImageParamsError(Exception):
    """图片送检参数不合法（客户端参数错误，非服务异常）。"""


async def check_public_text(
    db: AsyncSession,
    user_id: int,
    content: str,
    scene: WeChatSecurityScene,
) -> None:
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


CLOUD_STORAGE_HOST_SUFFIX = ".tcb.qcloud.la"


def _validate_image_pair(file_id: str, media_url: str) -> None:
    """确保送检 URL 与 fileID 指向同一云存储环境下的同一对象。

    fileID 形如 `cloud://<环境ID>[.<存储桶>]/<路径>`；临时 URL 域名为
    `<存储桶>[-<AppID>].tcb.qcloud.la`（官方 getdownloadtcbfilelink 示例：fileID
    `cloud://test2-4a89da.7465-test2-4a89da/A.png` 对应 `https://7465-test2-4a89da-1258717764.tcb.qcloud.la/A.png`，
    域名带 AppID 后缀，因此不能与存储桶做等值比较）。只比对路径时，「A 环境 fileID +
    B 环境同路径对象」也能通过——送检图就不是最终展示图，故 fileID 的环境段必须
    落在域名内，且路径一致。

    DNS 主机名大小写不敏感，而 `urlparse().hostname` 会把域名小写化，故环境段比对
    前统一 `.lower()`（路径保持原样，对象路径大小写有意义）；环境段含空段
    （如 `cloud://./posts/a.jpg`）直接拒绝，否则空段被滤掉后绑定形同虚设。无点形式
    `cloud://<环境ID>/<路径>` 的绑定强度仅为「域名包含环境段」，本项目客户端只发
    dot 形式（官方样例即是），故不额外收紧。
    """
    if not file_id.startswith("cloud://"):
        raise InvalidImageParamsError("图片 fileID 非法")
    cloud_scope, _, cloud_path = file_id[len("cloud://"):].partition("/")
    parsed = urlparse(media_url)
    host = parsed.hostname or ""
    host_scope = (
        host[: -len(CLOUD_STORAGE_HOST_SUFFIX)]
        if host.endswith(CLOUD_STORAGE_HOST_SUFFIX)
        else ""
    )
    if (
        not cloud_scope
        or not cloud_path
        or not host_scope
        or parsed.scheme != "https"
        or any(
            not part or part not in host_scope
            for part in cloud_scope.lower().split(".")
        )
    ):
        # 部署后若真实域名形态与假设不符，这里会全量触发；留 file_id 与解析出的 host
        # 便于一次定位（40014 本身不带可诊断信息）。三者均来自客户端，故用 %r 转义控制
        # 字符（否则换行可在消息体内伪造出不带前缀的日志行）并截断（防单条日志无限膨胀）。
        logger.warning(
            "Rejected image pair, file_id=%r host=%r scope=%r",
            file_id[:200],
            host[:200],
            cloud_scope[:200],
        )
        raise InvalidImageParamsError("图片临时 URL 与 fileID 不属于同一云存储环境")
    if not unquote(parsed.path).endswith("/" + cloud_path):
        raise InvalidImageParamsError("图片 URL 与 fileID 路径不匹配")


async def submit_post_images(
    db: AsyncSession,
    user_id: int,
    post_id: int,
    file_ids: list[str],
    media_urls: list[str],
) -> bool:
    """为帖子图片创建异步检测任务；本地 mock 直接通过。"""
    if len(file_ids) != len(media_urls) or not file_ids:
        raise InvalidImageParamsError("图片检测参数不完整")
    result = await db.execute(select(User.openid).where(User.id == user_id))
    openid = result.scalar_one_or_none()
    if not openid:
        raise ContentSecurityUnavailableError("用户 openid 不存在")

    traces: list[tuple[str, str]] = []
    try:
        for file_id, media_url in zip(file_ids, media_urls, strict=True):
            _validate_image_pair(file_id, media_url)
            trace_id = await wechat_client.media_check_async(
                media_url, openid, scene=WeChatSecurityScene.FORUM
            )
            if trace_id:
                traces.append((trace_id, file_id))
    except InvalidImageParamsError:
        # 客户端参数错误原样上抛，不混进「服务不可用」
        raise
    except Exception as exc:
        logger.warning(
            "Media security submission unavailable for user_id=%s post_id=%s",
            user_id,
            post_id,
            exc_info=True,
        )
        raise ContentSecurityUnavailableError from exc

    if not traces:
        db_post = await db.get(Post, post_id)
        if not db_post:
            # 帖子缺失时无法确认审核结论，fail closed 而非当作通过
            raise ContentSecurityUnavailableError("帖子不存在")
        db_post.moderation_status = PostModerationStatus.APPROVED
        return True
    for trace_id, file_id in traces:
        db.add(MediaModerationTask(
            resource_type=MediaModerationResourceType.POST,
            resource_id=post_id,
            trace_id=trace_id,
            file_id=file_id,
        ))
    return False


async def submit_parking_rental_images(
    db: AsyncSession,
    user_id: int,
    rental_id: int,
    file_ids: list[str],
    media_urls: list[str],
) -> bool:
    """为长期出租图片创建异步检测任务；本地 mock 直接通过。"""
    if len(file_ids) != len(media_urls) or not file_ids:
        raise InvalidImageParamsError("图片检测参数不完整")
    result = await db.execute(select(User.openid).where(User.id == user_id))
    openid = result.scalar_one_or_none()
    if not openid:
        raise ContentSecurityUnavailableError("用户 openid 不存在")
    traces: list[tuple[str, str]] = []
    try:
        for file_id, media_url in zip(file_ids, media_urls, strict=True):
            _validate_image_pair(file_id, media_url)
            trace_id = await wechat_client.media_check_async(
                media_url, openid, scene=WeChatSecurityScene.FORUM
            )
            if trace_id:
                traces.append((trace_id, file_id))
    except InvalidImageParamsError:
        raise
    except Exception as exc:
        logger.warning(
            "Media security submission unavailable for user_id=%s rental_id=%s",
            user_id, rental_id, exc_info=True,
        )
        raise ContentSecurityUnavailableError from exc

    if not traces:
        rental = await db.get(ParkingRental, rental_id)
        if not rental:
            raise ContentSecurityUnavailableError("出租信息不存在")
        rental.moderation_status = PostModerationStatus.APPROVED
        return True
    for trace_id, file_id in traces:
        db.add(MediaModerationTask(
            resource_type=MediaModerationResourceType.PARKING_RENTAL,
            resource_id=rental_id,
            trace_id=trace_id,
            file_id=file_id,
        ))
    return False


async def apply_media_result(
    db: AsyncSession, trace_id: str, suggestion: str, errcode: int
) -> bool:
    """幂等应用微信图片检测回调，并按资源类型分发审核结果。

    判定语义：``rejected`` 是吸收态——同一 ``trace_id`` 的后续回调不再改判；
    反之，非 ``pass`` 回调优先于已判定的 ``passed``（乱序/伪造的 pass 不能
    阻止撤回），因为公开内容只能向「不公开」方向单调收紧。
    """
    result = await db.execute(
        select(MediaModerationTask).where(MediaModerationTask.trace_id == trace_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        return False
    if task.status == MediaModerationTaskStatus.REJECTED:
        # 吸收态：重复或乱序回调不得把已拒绝的图片改回通过
        return True
    task.status = (
        MediaModerationTaskStatus.PASSED
        if errcode == 0 and suggestion == "pass"
        else MediaModerationTaskStatus.REJECTED
    )
    if task.resource_type == MediaModerationResourceType.POST:
        return await _apply_post_media_result(db, task)
    if task.resource_type == MediaModerationResourceType.PARKING_RENTAL:
        return await _apply_parking_rental_media_result(db, task)
    logger.error(
        "Unknown moderation resource type for trace_id=%r: %r",
        trace_id[:200], task.resource_type,
    )
    return False


async def _apply_post_media_result(
    db: AsyncSession,
    task: MediaModerationTask,
) -> bool:
    """将已判定的通用审核任务应用到帖子。"""
    post = await db.get(Post, task.resource_id)
    if not post:
        # 帖子已删除：无需撤回、重试也不会有结果，幂等视为已处理完（与
        # submit_post_images 对同一情形的 fail closed 口径不同，那是「无法确认结论」）
        return True
    if task.status == MediaModerationTaskStatus.REJECTED:
        post.moderation_status = PostModerationStatus.REJECTED
        return True
    statuses_result = await db.execute(
        select(MediaModerationTask.status).where(
            MediaModerationTask.resource_type == MediaModerationResourceType.POST,
            MediaModerationTask.resource_id == post.id,
        )
    )
    statuses = list(statuses_result.scalars())
    if statuses and all(
        status == MediaModerationTaskStatus.PASSED for status in statuses
    ):
        # approved 只能从 pending 进入；rejected 是单调终态
        if post.moderation_status == PostModerationStatus.PENDING:
            post.moderation_status = PostModerationStatus.APPROVED
    return True


async def _apply_parking_rental_media_result(
    db: AsyncSession,
    task: MediaModerationTask,
) -> bool:
    """将图片回调幂等应用到长期出租信息。"""
    rental = await db.get(ParkingRental, task.resource_id)
    if not rental:
        return True
    if task.status == MediaModerationTaskStatus.REJECTED:
        rental.moderation_status = PostModerationStatus.REJECTED
        return True
    result = await db.execute(
        select(MediaModerationTask.status).where(
            MediaModerationTask.resource_type
            == MediaModerationResourceType.PARKING_RENTAL,
            MediaModerationTask.resource_id == rental.id,
        )
    )
    statuses = list(result.scalars())
    if statuses and all(status == MediaModerationTaskStatus.PASSED for status in statuses):
        if rental.moderation_status == PostModerationStatus.PENDING:
            rental.moderation_status = PostModerationStatus.APPROVED
    return True

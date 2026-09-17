"""长期车位出租 API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_optional_user
from app.api.response import api_error, api_success
from app.core.moderation import PostModerationStatus
from app.core.parking import ParkingRentalListingType
from app.core.wechat import WeChatSecurityScene
from app.harness.tool_registry import dumps, strip_keys, tool
from app.schemas.parking_rental import ParkingRentalCreate, ParkingRentalUpdate
from app.services import content_security_service, parking_rental_service
from tools.mcp_server.decorator import mcp_tool

router = APIRouter(prefix="/parking-rentals", tags=["车位出租"])


def _format_list_parking_rentals(data) -> str:
    """删减：发布者 avatar（LLM 不需要）。保留分页、供需、审核状态和本人联系方式。"""
    return dumps(strip_keys(data, {"avatar"}))


def _format_create_parking_rental(data) -> str:
    """删减：发布者 avatar。返回新建的出租或求租信息。"""
    return dumps(strip_keys(data, {"avatar"}))


def _format_get_parking_rental(data) -> str:
    """删减：发布者 avatar。其余车位租赁详情原样保留。"""
    return dumps(strip_keys(data, {"avatar"}))


def _format_update_parking_rental(data) -> str:
    """删减：发布者 avatar。返回更新后的车位租赁信息。"""
    return dumps(strip_keys(data, {"avatar"}))


def _format_get_parking_rental_contact(data) -> str:
    """无删减：返回登录用户按需查询到的联系方式。"""
    return dumps(data)


@router.get("")
@mcp_tool(result_formatter=_format_list_parking_rentals)
@tool(result_formatter=_format_list_parking_rentals)
async def list_parking_rentals(
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 20,
    area: Annotated[str | None, Query(max_length=8)] = None,
    nearby_building: Annotated[str | None, Query(max_length=32)] = None,
    listing_type: ParkingRentalListingType = ParkingRentalListingType.OFFER,
    mine: bool = False,
    db: AsyncSession = Depends(get_db),
    user_id: int | None = Depends(get_optional_user),
):
    """游客可浏览公开出租信息；mine=true 时仅返回本人发布。"""
    if mine and user_id is None:
        return api_error(40101, "请先登录", status_code=401)
    result = await parking_rental_service.list_rentals(
        db, user_id, page, size, area, nearby_building, mine, listing_type
    )
    return api_success(result)


@router.post("")
@mcp_tool(result_formatter=_format_create_parking_rental)
@tool(result_formatter=_format_create_parking_rental)
async def create_parking_rental(
    data: ParkingRentalCreate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """发布车位出租或求租信息。"""
    if (
        data.listing_type == ParkingRentalListingType.OFFER
        and not parking_rental_service.is_valid_spot(data.spot_id or "")
    ):
        return api_error(40021, "车位编号不存在，请从地图车位中选择")
    text = "\n".join(filter(None, [
        data.spot_id, data.area, data.nearby_building, data.description, data.contact,
    ]))
    try:
        await content_security_service.check_public_text(
            db, user_id, text, scene=WeChatSecurityScene.FORUM
        )
    except content_security_service.UnsafeContentError:
        return api_error(40010, "发布内容含有违规信息，请修改后重试")
    except content_security_service.ContentSecurityUnavailableError:
        return api_error(50310, "内容安全验证暂时不可用，请稍后重试", status_code=503)

    rental, response = await parking_rental_service.create(db, user_id, data)
    if data.listing_type == ParkingRentalListingType.OFFER and data.images:
        try:
            approved = await content_security_service.submit_parking_rental_images(
                db, user_id, rental.id, data.images, data.image_urls
            )
            if approved:
                response.moderation_status = PostModerationStatus.APPROVED
        except content_security_service.InvalidImageParamsError:
            await db.rollback()
            return api_error(40014, "图片送检参数有误，请重新选择图片后发布")
        except content_security_service.ContentSecurityUnavailableError:
            await db.rollback()
            return api_error(50310, "内容安全验证暂时不可用，请稍后重试", status_code=503)
        await db.flush()
        response = await parking_rental_service.get(db, rental.id, user_id)
    return api_success(response)


@router.get("/{rental_id}")
@mcp_tool(result_formatter=_format_get_parking_rental)
@tool(result_formatter=_format_get_parking_rental)
async def get_parking_rental(
    rental_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int | None = Depends(get_optional_user),
):
    result = await parking_rental_service.get(db, rental_id, user_id)
    return api_success(result)


@router.patch("/{rental_id}")
@mcp_tool(result_formatter=_format_update_parking_rental)
@tool(result_formatter=_format_update_parking_rental)
async def update_parking_rental(
    rental_id: int,
    data: ParkingRentalUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """编辑或上下架本人出租信息。"""
    if not await parking_rental_service.is_owner(db, rental_id, user_id):
        return api_error(40301, "无权编辑此出租信息")
    changed_text = "\n".join(
        str(value)
        for key, value in data.model_dump(exclude_unset=True).items()
        if key not in {"status", "images", "image_urls"} and value
    )
    if changed_text:
        try:
            await content_security_service.check_public_text(
                db, user_id, changed_text, scene=WeChatSecurityScene.FORUM
            )
        except content_security_service.UnsafeContentError:
            return api_error(40010, "发布内容含有违规信息，请修改后重试")
        except content_security_service.ContentSecurityUnavailableError:
            return api_error(50310, "内容安全验证暂时不可用，请稍后重试", status_code=503)
    update_result = await parking_rental_service.update(db, rental_id, user_id, data)
    if not update_result:
        return api_error(40301, "无权编辑此出租信息")
    result, new_image_pairs = update_result
    if new_image_pairs:
        new_file_ids = [pair[0] for pair in new_image_pairs]
        new_media_urls = [pair[1] for pair in new_image_pairs]
        try:
            approved = await content_security_service.submit_parking_rental_images(
                db, user_id, rental_id, new_file_ids, new_media_urls
            )
            if approved:
                result.moderation_status = PostModerationStatus.APPROVED
        except content_security_service.InvalidImageParamsError:
            await db.rollback()
            return api_error(40014, "图片送检参数有误，请重新选择图片后保存")
        except content_security_service.ContentSecurityUnavailableError:
            await db.rollback()
            return api_error(50310, "内容安全验证暂时不可用，请稍后重试", status_code=503)
        await db.flush()
        result = await parking_rental_service.get(db, rental_id, user_id)
    return api_success(result)


@router.get("/{rental_id}/contact")
@mcp_tool(result_formatter=_format_get_parking_rental_contact)
@tool(result_formatter=_format_get_parking_rental_contact)
async def get_parking_rental_contact(
    rental_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """登录后按需读取联系方式，游客列表和详情不返回该字段。"""
    contact = await parking_rental_service.contact(db, rental_id, user_id)
    if contact is None:
        return api_error(40401, "出租信息不存在或已下架", status_code=404)
    return api_success({"contact": contact})

"""长期车位出租 API。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_optional_user
from app.api.response import api_error, api_success
from app.core.moderation import MediaModerationTaskStatus, PostModerationStatus
from app.core.parking import ParkingRentalListingType
from app.core.wechat import WeChatSecurityScene
from app.schemas.parking_rental import ParkingRentalCreate, ParkingRentalUpdate
from app.services import content_security_service, parking_rental_service

router = APIRouter(prefix="/parking-rentals", tags=["车位出租"])


@router.get("")
async def list_parking_rentals(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=50),
    area: str | None = Query(default=None, max_length=8),
    nearby_building: str | None = Query(default=None, max_length=32),
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
                response.image_moderation_statuses = [
                    MediaModerationTaskStatus.PASSED for _ in data.images
                ]
        except content_security_service.InvalidImageParamsError:
            await db.rollback()
            return api_error(40014, "图片送检参数有误，请重新选择图片后发布")
        except content_security_service.ContentSecurityUnavailableError:
            await db.rollback()
            return api_error(50310, "内容安全验证暂时不可用，请稍后重试", status_code=503)
    return api_success(response)


@router.get("/{rental_id}")
async def get_parking_rental(
    rental_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int | None = Depends(get_optional_user),
):
    result = await parking_rental_service.get(db, rental_id, user_id)
    return api_success(result)


@router.patch("/{rental_id}")
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
    result = await parking_rental_service.update(db, rental_id, user_id, data)
    if not result:
        return api_error(40301, "无权编辑此出租信息")
    if data.images:
        try:
            approved = await content_security_service.submit_parking_rental_images(
                db, user_id, rental_id, data.images, data.image_urls or []
            )
            if approved:
                result.moderation_status = PostModerationStatus.APPROVED
                result.image_moderation_statuses = [
                    MediaModerationTaskStatus.PASSED for _ in data.images
                ]
        except content_security_service.InvalidImageParamsError:
            await db.rollback()
            return api_error(40014, "图片送检参数有误，请重新选择图片后保存")
        except content_security_service.ContentSecurityUnavailableError:
            await db.rollback()
            return api_error(50310, "内容安全验证暂时不可用，请稍后重试", status_code=503)
    return api_success(result)


@router.get("/{rental_id}/contact")
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

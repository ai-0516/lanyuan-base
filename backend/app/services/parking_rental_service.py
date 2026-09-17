"""长期车位出租业务逻辑。"""

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.moderation import (
    MediaModerationResourceType,
    MediaModerationTaskStatus,
    PostModerationStatus,
)
from app.core.parking import ParkingRentalListingType, ParkingRentalStatus
from app.data.parking_spots import PARKING_SPOT_IDS
from app.models.parking_rental import ParkingRental
from app.models.post import MediaModerationTask
from app.models.user import User
from app.schemas.common import UserBrief
from app.schemas.parking_rental import (
    ParkingRentalCreate,
    ParkingRentalListResponse,
    ParkingRentalResponse,
    ParkingRentalUpdate,
)


async def _response(db: AsyncSession, rental: ParkingRental, current_user_id: int | None):
    user = await db.get(User, rental.user_id)
    if not user:
        return None
    images = rental.images if isinstance(rental.images, list) else []
    statuses = []
    if images and rental.user_id == current_user_id:
        result = await db.execute(
            select(MediaModerationTask.file_id, MediaModerationTask.status).where(
                MediaModerationTask.resource_type
                == MediaModerationResourceType.PARKING_RENTAL,
                MediaModerationTask.resource_id == rental.id,
            )
        )
        by_file = dict(result.all())
        # 新图片一定有对应任务；没有任务的是历史已通过图片（例如本地 mock
        # 环境直接通过），不能因为同一信息里有新图待审就误标为 pending。
        fallback = MediaModerationTaskStatus.PASSED
        statuses = [MediaModerationTaskStatus(by_file.get(image, fallback)) for image in images]
    return ParkingRentalResponse(
        id=rental.id,
        user=UserBrief(id=user.id, nickname=user.nickname, avatar=user.avatar),
        listing_type=rental.listing_type,
        spot_id=rental.spot_id,
        area=rental.area,
        nearby_building=rental.nearby_building,
        description=rental.description,
        images=images,
        status=rental.status,
        moderation_status=rental.moderation_status,
        image_moderation_statuses=statuses,
        is_owner=rental.user_id == current_user_id,
        contact=rental.contact if rental.user_id == current_user_id else None,
        created_at=rental.created_at,
    )


async def create(db: AsyncSession, user_id: int, data: ParkingRentalCreate):
    rental = ParkingRental(
        user_id=user_id,
        listing_type=data.listing_type,
        spot_id=data.spot_id,
        area=data.area,
        nearby_building=data.nearby_building,
        description=data.description,
        contact=data.contact,
        images=data.images,
        moderation_status=(PostModerationStatus.PENDING if data.images else PostModerationStatus.APPROVED),
    )
    db.add(rental)
    await db.flush()
    # MySQL/asyncmy 不会像 SQLite RETURNING 一样自动填充服务端默认时间；
    # 显式刷新，避免读取 created_at 时触发异步上下文外的隐式 IO。
    await db.refresh(rental)
    return rental, await _response(db, rental, user_id)


def is_valid_spot(spot_id: str) -> bool:
    return spot_id in PARKING_SPOT_IDS


async def is_owner(db: AsyncSession, rental_id: int, user_id: int) -> bool:
    result = await db.execute(select(ParkingRental.id).where(
        ParkingRental.id == rental_id,
        ParkingRental.user_id == user_id,
    ))
    return result.scalar_one_or_none() is not None


async def list_rentals(
    db: AsyncSession, current_user_id: int | None, page: int, size: int,
    area: str | None, nearby_building: str | None, mine: bool,
    listing_type: ParkingRentalListingType,
):
    filters = [ParkingRental.listing_type == listing_type]
    if mine and current_user_id is not None:
        filters.append(ParkingRental.user_id == current_user_id)
    else:
        visibility = ParkingRental.moderation_status == PostModerationStatus.APPROVED
        if current_user_id is not None:
            visibility = or_(visibility, ParkingRental.user_id == current_user_id)
        filters.extend([ParkingRental.status == ParkingRentalStatus.ACTIVE, visibility])
    if area:
        filters.append(ParkingRental.area == area.upper())
    if nearby_building:
        filters.append(ParkingRental.nearby_building.ilike(f"%{nearby_building}%"))
    total = (await db.execute(select(func.count(ParkingRental.id)).where(*filters))).scalar() or 0
    result = await db.execute(
        select(ParkingRental).where(*filters)
        .order_by(ParkingRental.created_at.desc(), ParkingRental.id.desc())
        .offset((page - 1) * size).limit(size)
    )
    items = []
    for rental in result.scalars():
        item = await _response(db, rental, current_user_id)
        if item:
            items.append(item)
    return ParkingRentalListResponse(items=items, total=total, page=page, size=size)


async def get(db: AsyncSession, rental_id: int, current_user_id: int | None):
    visibility = (
        (ParkingRental.status == ParkingRentalStatus.ACTIVE)
        & (ParkingRental.moderation_status == PostModerationStatus.APPROVED)
    )
    if current_user_id is not None:
        visibility = or_(visibility, ParkingRental.user_id == current_user_id)
    result = await db.execute(select(ParkingRental).where(ParkingRental.id == rental_id, visibility))
    rental = result.scalar_one_or_none()
    return await _response(db, rental, current_user_id) if rental else None


async def update(db: AsyncSession, rental_id: int, user_id: int, data: ParkingRentalUpdate):
    result = await db.execute(
        select(ParkingRental).where(ParkingRental.id == rental_id, ParkingRental.user_id == user_id)
    )
    rental = result.scalar_one_or_none()
    if not rental:
        return None
    old_images = rental.images if isinstance(rental.images, list) else []
    values = data.model_dump(exclude_unset=True, exclude={"image_urls"})
    new_image_pairs: list[tuple[str, str]] = []
    if rental.listing_type == ParkingRentalListingType.OFFER:
        values.pop("area", None)
        values.pop("nearby_building", None)
    elif values.get("images"):
        raise ValueError("求租信息不能上传图片")
    if values.get("area"):
        values["area"] = values["area"].strip().upper()
    if "images" in values:
        final_images = values["images"]
        old_image_set = set(old_images)
        new_image_pairs = [
            (file_id, media_url)
            for file_id, media_url in zip(final_images, data.image_urls or [], strict=True)
            if file_id not in old_image_set
        ]
        removed_task_filter = [
            MediaModerationTask.resource_type
            == MediaModerationResourceType.PARKING_RENTAL,
            MediaModerationTask.resource_id == rental.id,
        ]
        if final_images:
            removed_task_filter.append(MediaModerationTask.file_id.not_in(final_images))
        await db.execute(delete(MediaModerationTask).where(*removed_task_filter))
        if new_image_pairs:
            rental.moderation_status = PostModerationStatus.PENDING
        else:
            statuses = list((await db.execute(select(MediaModerationTask.status).where(
                MediaModerationTask.resource_type
                == MediaModerationResourceType.PARKING_RENTAL,
                MediaModerationTask.resource_id == rental.id,
            ))).scalars())
            rental.moderation_status = (
                PostModerationStatus.REJECTED
                if MediaModerationTaskStatus.REJECTED in statuses
                else PostModerationStatus.PENDING
                if MediaModerationTaskStatus.PENDING in statuses
                else PostModerationStatus.APPROVED
            )
    for field, value in values.items():
        setattr(rental, field, value)
    await db.flush()
    return await _response(db, rental, user_id), new_image_pairs


async def contact(db: AsyncSession, rental_id: int, current_user_id: int):
    public = (
        (ParkingRental.status == ParkingRentalStatus.ACTIVE)
        & (ParkingRental.moderation_status == PostModerationStatus.APPROVED)
    )
    result = await db.execute(select(ParkingRental.contact).where(
        ParkingRental.id == rental_id,
        or_(public, ParkingRental.user_id == current_user_id),
    ))
    return result.scalar_one_or_none()

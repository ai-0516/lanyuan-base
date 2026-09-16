"""长期车位出租业务逻辑。"""

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.moderation import MediaModerationTaskStatus, PostModerationStatus
from app.core.parking import ParkingRentalStatus
from app.data.parking_spots import PARKING_SPOT_IDS
from app.models.parking_rental import ParkingRental, ParkingRentalMediaModerationTask
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
            select(ParkingRentalMediaModerationTask.file_id, ParkingRentalMediaModerationTask.status)
            .where(ParkingRentalMediaModerationTask.rental_id == rental.id)
        )
        by_file = dict(result.all())
        fallback = (
            MediaModerationTaskStatus.PASSED
            if rental.moderation_status == PostModerationStatus.APPROVED
            else MediaModerationTaskStatus.PENDING
        )
        statuses = [MediaModerationTaskStatus(by_file.get(image, fallback)) for image in images]
    return ParkingRentalResponse(
        id=rental.id,
        user=UserBrief(id=user.id, nickname=user.nickname, avatar=user.avatar),
        spot_id=rental.spot_id,
        area=rental.area,
        nearby_building=rental.nearby_building,
        price_monthly=rental.price_monthly,
        rental_term=rental.rental_term,
        description=rental.description,
        images=images,
        status=rental.status,
        moderation_status=rental.moderation_status,
        image_moderation_statuses=statuses,
        is_owner=rental.user_id == current_user_id,
        created_at=rental.created_at,
    )


async def create(db: AsyncSession, user_id: int, data: ParkingRentalCreate):
    rental = ParkingRental(
        user_id=user_id,
        spot_id=data.spot_id,
        area=data.spot_id[0].upper(),
        nearby_building=data.nearby_building,
        price_monthly=data.price_monthly,
        rental_term=data.rental_term,
        description=data.description,
        contact=data.contact,
        images=data.images,
        moderation_status=(PostModerationStatus.PENDING if data.images else PostModerationStatus.APPROVED),
    )
    db.add(rental)
    await db.flush()
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
    area: str | None, nearby_building: str | None, min_price: int | None,
    max_price: int | None, mine: bool,
):
    filters = []
    if mine and current_user_id is not None:
        filters.append(ParkingRental.user_id == current_user_id)
    else:
        filters.extend([
            ParkingRental.status == ParkingRentalStatus.ACTIVE,
            ParkingRental.moderation_status == PostModerationStatus.APPROVED,
        ])
    if area:
        filters.append(ParkingRental.area == area.upper())
    if nearby_building:
        filters.append(ParkingRental.nearby_building.ilike(f"%{nearby_building}%"))
    if min_price is not None:
        filters.append(ParkingRental.price_monthly >= min_price)
    if max_price is not None:
        filters.append(ParkingRental.price_monthly <= max_price)
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
    values = data.model_dump(exclude_unset=True, exclude={"image_urls"})
    if "images" in values:
        await db.execute(delete(ParkingRentalMediaModerationTask).where(
            ParkingRentalMediaModerationTask.rental_id == rental.id
        ))
        rental.moderation_status = (
            PostModerationStatus.PENDING
            if values["images"]
            else PostModerationStatus.APPROVED
        )
    for field, value in values.items():
        setattr(rental, field, value)
    await db.flush()
    return await _response(db, rental, user_id)


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

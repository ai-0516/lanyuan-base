"""长期车位出租 API 模型。"""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.core.moderation import MediaModerationTaskStatus, PostModerationStatus
from app.core.parking import ParkingRentalStatus
from app.schemas.common import UserBrief


class ParkingRentalCreate(BaseModel):
    spot_id: str = Field(min_length=2, max_length=16, pattern=r"^[A-Za-z][A-Za-z0-9-]+$")
    nearby_building: str | None = Field(default=None, max_length=32)
    price_monthly: int = Field(gt=0, le=100000)
    rental_term: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1000)
    contact: str = Field(min_length=1, max_length=128)
    images: list[str] = Field(default_factory=list, max_length=9)
    image_urls: list[str] = Field(default_factory=list, max_length=9)

    @model_validator(mode="after")
    def validate_images(self):
        if len(self.images) != len(self.image_urls):
            raise ValueError("images 与 image_urls 数量必须一致")
        self.spot_id = self.spot_id.upper()
        self.nearby_building = self.nearby_building.strip() if self.nearby_building else None
        return self


class ParkingRentalUpdate(BaseModel):
    nearby_building: str | None = Field(default=None, max_length=32)
    price_monthly: int | None = Field(default=None, gt=0, le=100000)
    rental_term: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, min_length=1, max_length=1000)
    contact: str | None = Field(default=None, min_length=1, max_length=128)
    status: ParkingRentalStatus | None = None
    images: list[str] | None = Field(default=None, max_length=9)
    image_urls: list[str] | None = Field(default=None, max_length=9)

    @model_validator(mode="after")
    def validate_images(self):
        if (self.images is None) != (self.image_urls is None):
            raise ValueError("images 与 image_urls 必须同时提供")
        if self.images is not None and len(self.images) != len(self.image_urls):
            raise ValueError("images 与 image_urls 数量必须一致")
        return self


class ParkingRentalResponse(BaseModel):
    id: int
    user: UserBrief
    spot_id: str
    area: str
    nearby_building: str | None = None
    price_monthly: int
    rental_term: str
    description: str
    images: list[str] = Field(default_factory=list)
    status: ParkingRentalStatus
    moderation_status: PostModerationStatus
    image_moderation_statuses: list[MediaModerationTaskStatus] = Field(default_factory=list)
    is_owner: bool = False
    created_at: datetime


class ParkingRentalListResponse(BaseModel):
    items: list[ParkingRentalResponse]
    total: int
    page: int
    size: int


class ParkingRentalContactResponse(BaseModel):
    contact: str

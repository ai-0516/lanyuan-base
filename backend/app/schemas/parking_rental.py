"""长期车位出租 API 模型。"""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.core.moderation import MediaModerationTaskStatus, PostModerationStatus
from app.core.parking import ParkingRentalListingType, ParkingRentalStatus
from app.schemas.common import UserBrief


class ParkingRentalCreate(BaseModel):
    listing_type: ParkingRentalListingType = ParkingRentalListingType.OFFER
    spot_id: str | None = Field(default=None, min_length=2, max_length=16, pattern=r"^[A-Za-z][A-Za-z0-9-]+$")
    area: str | None = Field(default=None, min_length=1, max_length=8)
    nearby_building: str | None = Field(default=None, max_length=32)
    description: str = Field(min_length=1, max_length=1000)
    contact: str = Field(min_length=1, max_length=128)
    images: list[str] = Field(default_factory=list, max_length=9)
    image_urls: list[str] = Field(default_factory=list, max_length=9)

    @model_validator(mode="after")
    def validate_images(self):
        if len(self.images) != len(self.image_urls):
            raise ValueError("images 与 image_urls 数量必须一致")
        if self.listing_type == ParkingRentalListingType.OFFER:
            if not self.spot_id:
                raise ValueError("出租信息必须填写车位编号")
            self.spot_id = self.spot_id.upper()
            self.area = self.spot_id[0]
            self.nearby_building = None
        else:
            if not self.area or not self.area.strip():
                raise ValueError("求租信息必须填写期望区域")
            if self.images:
                raise ValueError("求租信息不能上传图片")
            self.spot_id = None
            self.area = self.area.strip().upper()
        self.nearby_building = self.nearby_building.strip() if self.nearby_building else None
        return self


class ParkingRentalUpdate(BaseModel):
    area: str | None = Field(default=None, min_length=1, max_length=8)
    nearby_building: str | None = Field(default=None, max_length=32)
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
    listing_type: ParkingRentalListingType
    spot_id: str | None = None
    area: str
    nearby_building: str | None = None
    description: str
    images: list[str] = Field(default_factory=list)
    status: ParkingRentalStatus
    moderation_status: PostModerationStatus
    image_moderation_statuses: list[MediaModerationTaskStatus] = Field(default_factory=list)
    is_owner: bool = False
    contact: str | None = None
    created_at: datetime


class ParkingRentalListResponse(BaseModel):
    items: list[ParkingRentalResponse]
    total: int
    page: int
    size: int


class ParkingRentalContactResponse(BaseModel):
    contact: str

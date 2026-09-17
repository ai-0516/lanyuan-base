"""长期车位出租模型。"""

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base
from app.core.moderation import PostModerationStatus
from app.core.parking import ParkingRentalListingType, ParkingRentalStatus


class ParkingRental(Base):
    __tablename__ = "parking_rentals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    listing_type = Column(String(16), nullable=False, default=ParkingRentalListingType.OFFER, index=True)
    spot_id = Column(String(16), nullable=True, index=True)
    area = Column(String(8), nullable=False, index=True)
    nearby_building = Column(String(32), nullable=True, index=True)
    price_monthly = Column(Integer, nullable=True, index=True)
    rental_term = Column(String(64), nullable=True)
    description = Column(Text, nullable=False)
    contact = Column(String(128), nullable=False)
    images = Column(JSON, nullable=False, default=list)
    status = Column(String(16), nullable=False, default=ParkingRentalStatus.ACTIVE, index=True)
    moderation_status = Column(
        String(16), nullable=False, default=PostModerationStatus.APPROVED, index=True
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

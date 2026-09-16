"""长期车位出租模型。"""

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base
from app.core.moderation import MediaModerationTaskStatus, PostModerationStatus
from app.core.parking import ParkingRentalStatus


class ParkingRental(Base):
    __tablename__ = "parking_rentals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    spot_id = Column(String(16), nullable=False, index=True)
    area = Column(String(8), nullable=False, index=True)
    nearby_building = Column(String(32), nullable=True, index=True)
    price_monthly = Column(Integer, nullable=False, index=True)
    rental_term = Column(String(64), nullable=False)
    description = Column(Text, nullable=False)
    contact = Column(String(128), nullable=False)
    images = Column(JSON, nullable=False, default=list)
    status = Column(String(16), nullable=False, default=ParkingRentalStatus.ACTIVE, index=True)
    moderation_status = Column(
        String(16), nullable=False, default=PostModerationStatus.APPROVED, index=True
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ParkingRentalMediaModerationTask(Base):
    __tablename__ = "parking_rental_media_moderation_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rental_id = Column(
        Integer, ForeignKey("parking_rentals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trace_id = Column(String(128), nullable=False, unique=True, index=True)
    file_id = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default=MediaModerationTaskStatus.PENDING)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

"""帖子模型"""

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, JSON, func

from app.core.database import Base
from app.core.moderation import (
    MediaModerationResourceType,
    MediaModerationTaskStatus,
    PostModerationStatus,
)


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    content = Column(Text, nullable=False)
    images = Column(JSON, nullable=False, default=list)
    moderation_status = Column(
        String(16), nullable=False, default=PostModerationStatus.APPROVED, index=True
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class MediaModerationTask(Base):
    __tablename__ = "media_moderation_tasks"
    __table_args__ = (
        Index("ix_media_moderation_tasks_resource", "resource_type", "resource_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    resource_type = Column(
        String(32), nullable=False, default=MediaModerationResourceType.POST
    )
    resource_id = Column(Integer, nullable=False)
    trace_id = Column(String(128), nullable=False, unique=True, index=True)
    file_id = Column(Text, nullable=False)
    status = Column(
        String(16), nullable=False, default=MediaModerationTaskStatus.PENDING
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

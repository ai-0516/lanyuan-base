"""帖子模型"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, JSON, func

from app.core.database import Base
from app.core.moderation import MediaModerationTaskStatus, PostModerationStatus


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

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    trace_id = Column(String(128), nullable=False, unique=True, index=True)
    file_id = Column(Text, nullable=False)
    status = Column(
        String(16), nullable=False, default=MediaModerationTaskStatus.PENDING
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

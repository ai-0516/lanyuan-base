"""通知模型"""

from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, func

from app.core.database import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    type = Column(Enum("like", "comment", "reply", name="notification_type"), nullable=False)
    from_user_id = Column(Integer, nullable=False)
    post_id = Column(Integer, nullable=False)
    comment_id = Column(Integer, nullable=True)
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

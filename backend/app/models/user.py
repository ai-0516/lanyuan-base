"""用户模型"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, func

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    openid = Column(String(64), unique=True, nullable=False, index=True)
    unionid = Column(String(64), nullable=True)
    nickname = Column(String(32), nullable=False, default="兰园业主")
    avatar = Column(String(256), nullable=False, default="")
    community = Column(String(64), nullable=True)
    building = Column(String(16), nullable=True)
    unit = Column(String(16), nullable=True)
    room = Column(String(16), nullable=True)
    bio = Column(String(200), nullable=True, default="")
    show_building = Column(Boolean, default=True)
    show_room = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

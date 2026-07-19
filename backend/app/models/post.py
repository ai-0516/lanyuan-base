"""帖子模型"""

from sqlalchemy import Column, DateTime, Integer, Text, func
from sqlalchemy.dialects.sqlite import JSON

from app.core.database import Base


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    content = Column(Text, nullable=False)
    images = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

"""评论模型"""

from sqlalchemy import Column, DateTime, Integer, String, func

from app.core.database import Base


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False)
    parent_comment_id = Column(Integer, nullable=True)
    content = Column(String(500), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

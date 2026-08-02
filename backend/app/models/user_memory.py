"""跨会话记忆模型"""

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from app.core.database import Base


class UserMemory(Base):
    __tablename__ = "user_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True, comment="用户 ID")
    name = Column(String(100), nullable=False, comment="短标识（kebab-case）")
    type = Column(String(20), nullable=False, default="user", comment="记忆类型: user / reference")
    description = Column(String(255), nullable=False, comment="一行摘要（用于索引）")
    body = Column(Text, nullable=False, comment="完整内容（按需加载）")
    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")

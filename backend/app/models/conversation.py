"""AI 对话模型"""

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from app.core.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    title = Column(String(100), nullable=True, default="")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, nullable=False, index=True)
    role = Column(String(20), nullable=False, index=True)
    content = Column(Text, nullable=True)
    tool_calls = Column(Text, nullable=True)
    tool_call_id = Column(String(100), nullable=True)
    tool_name = Column(String(100), nullable=True)  # tool 消息的工具名（review #53：回填时直接入库）
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

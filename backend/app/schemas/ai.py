"""AI 对话相关 Pydantic 模型"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class MessageItem(BaseModel):
    id: int
    role: str  # user / assistant / tool
    content: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionResponse(BaseModel):
    session_id: int
    title: str = ""
    messages: List[MessageItem] = []


class ChatRequest(BaseModel):
    session_id: int
    message: str

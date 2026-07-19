"""评论相关 Pydantic 模型"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.schemas.common import ReplyTo, UserBrief


class CommentCreate(BaseModel):
    content: str
    parent_comment_id: Optional[int] = None


class CommentResponse(BaseModel):
    id: int
    user: UserBrief
    content: str
    reply_to: Optional[ReplyTo] = None
    created_at: datetime

    model_config = {"from_attributes": True}

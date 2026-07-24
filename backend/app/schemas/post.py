"""帖子相关 Pydantic 模型"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from app.schemas.common import ReplyTo, UserBrief


class PostCreate(BaseModel):
    content: str
    images: List[str] = []


class CommentItem(BaseModel):
    id: int
    user: UserBrief
    content: str
    reply_to: Optional[ReplyTo] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PostResponse(BaseModel):
    id: int
    user: UserBrief
    content: str
    images: List[str] = []
    liked: bool = False
    comments: List[CommentItem] = []
    likers: List[UserBrief] = []
    created_at: datetime

    model_config = {"from_attributes": True}


class PostListResponse(BaseModel):
    items: List[PostResponse]
    total: int
    page: int
    size: int

"""通知相关 Pydantic 模型"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.schemas.common import UserBrief


class NotificationResponse(BaseModel):
    id: int
    type: str  # like / comment / reply
    from_user: UserBrief
    post_id: int
    post_title: str = ""
    comment_id: Optional[int] = None
    is_read: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationCount(BaseModel):
    count: int

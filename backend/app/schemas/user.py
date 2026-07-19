"""用户相关 Pydantic 模型"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class UserCreate(BaseModel):
    code: str


class UserUpdate(BaseModel):
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    community: Optional[str] = None
    building: Optional[str] = None
    unit: Optional[str] = None
    room: Optional[str] = None
    bio: Optional[str] = None
    show_building: Optional[bool] = None
    show_room: Optional[bool] = None


class LoginResponse(BaseModel):
    token: str
    user: "UserResponse"


class UserResponse(BaseModel):
    id: int
    openid: str
    nickname: str
    avatar: str
    community: Optional[str] = None
    building: Optional[str] = None
    unit: Optional[str] = None
    room: Optional[str] = None
    bio: Optional[str] = None
    show_building: bool = True
    show_room: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class UserPublic(BaseModel):
    """公开用户信息（隐藏房号等敏感信息）"""
    id: int
    nickname: str
    avatar: str
    community: Optional[str] = None
    building: Optional[str] = None  # show_building 控制逻辑在 API 层 profile.py
    bio: Optional[str] = None

    model_config = {"from_attributes": True}

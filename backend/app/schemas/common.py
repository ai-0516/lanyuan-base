"""通用 Pydantic 模型（跨模块共享的类型）"""

from pydantic import BaseModel


class UserBrief(BaseModel):
    id: int
    nickname: str
    avatar: str

    model_config = {"from_attributes": True}


class ReplyTo(BaseModel):
    user_id: int
    nickname: str


class SuccessResponse(BaseModel):
    success: bool = True


class ApiResponse(BaseModel):
    """统一响应格式"""
    code: int = 0
    data: dict | list | None = None
    message: str = "ok"

"""个人中心/编辑资料 API"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.user import UserPublic, UserResponse, UserUpdate

router = APIRouter(tags=["用户"])


@router.get("/user/me", response_model=UserResponse)
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取当前用户信息"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    return user


@router.put("/user/me", response_model=UserResponse)
async def update_my_profile(
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """更新个人资料"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return {"code": 40401, "message": "用户不存在"}

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)

    db.add(user)
    await db.flush()
    return user


@router.get("/users/{user_id}", response_model=UserPublic)
async def get_user_public(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """查看用户公开信息（隐藏房号）"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return {"code": 40401, "message": "用户不存在"}

    return UserPublic(
        id=user.id,
        nickname=user.nickname,
        avatar=user.avatar,
        community=user.community,
        building=user.building if user.show_building else None,
        bio=user.bio,
    )


@router.post("/user/logout")
async def logout(
    user_id: int = Depends(get_current_user),
):
    """退出登录"""
    # JWT 无状态，前端清除 token 即可
    return {"success": True}

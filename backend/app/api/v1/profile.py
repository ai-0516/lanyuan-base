"""个人中心/编辑资料 API"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_error, api_success
from app.harness.tool_registry import tool
from app.models.user import User
from app.schemas.user import UserPublic, UserUpdate

router = APIRouter(tags=["用户"])


def _format_user_profile(data) -> str:
    """我的资料（get/update 共用）→ LLM 摘要

    只暴露 LLM 需要的公开资料；openid/unionid/房号（room/unit）等隐私字段不进摘要。
    """
    if not data:
        return "未找到该用户资料"
    building = data.get("building") or "未设置"
    if not data.get("show_building", True):
        building = "未公开"
    return (
        f"昵称：{data.get('nickname', '?')}\n"
        f"小区：{data.get('community') or '未设置'}\n"
        f"楼栋：{building}\n"
        f"简介：{data.get('bio') or '无'}"
    )


def _format_user_public(data) -> str:
    """他人公开资料 → LLM 摘要（不含 base64 头像）"""
    if not data:
        return "该用户不存在"
    building = data.get("building") or "未公开"
    return (
        f"用户 #{data.get('id', '?')}：{data.get('nickname', '?')}\n"
        f"小区：{data.get('community') or '未设置'}\n"
        f"楼栋：{building}\n"
        f"简介：{data.get('bio') or '无'}"
    )


@router.get("/user/me")
@tool(result_formatter=_format_user_profile)
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取当前登录用户的个人信息。"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    return api_success(user)


@router.put("/user/me")
@tool(result_formatter=_format_user_profile)
async def update_my_profile(
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """更新个人资料，包括昵称、头像、个人简介、小区、楼栋信息等。只传需要修改的字段即可。"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return api_error(40401, "用户不存在")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user, key, value)

    db.add(user)
    await db.flush()
    return api_success(user)


@router.get("/users/{user_id}")
@tool(result_formatter=_format_user_public)
async def get_user_public(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """查看某个用户的公开信息（不显示房号等隐私数据）。"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        # 查无此人是正常查询结果（code=0 + data=null），不是业务失败。
        # LLM 收到 "null" = 查询成功但用户不存在，据此调整策略（告知用户/换 ID 重查）。
        return api_success(None)

    return api_success(
        UserPublic(
            id=user.id,
            nickname=user.nickname,
            avatar=user.avatar,
            community=user.community,
            building=user.building if user.show_building else None,
            bio=user.bio,
        )
    )


# ── 注销 ──
# JWT 无状态，退出登录由前端清除本地 token 即可
# 无需后端接口

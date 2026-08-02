"""记忆管理 API（跨会话记忆 #9）

同时作为 LLM tool（@tool）和 REST API 使用：
- GET /memory        → 查看自己的记忆列表
- POST /memory       → 添加记忆
- DELETE /memory/{id} → 删除记忆
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_error, api_success
from app.harness.memory import VALID_TYPES, MemoryLimitError, get_provider
from app.harness.tool_registry import tool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["记忆"])


class MemoryCreate(BaseModel):
    name: str
    type: str = "user"
    description: str
    body: str


def _to_dict(m) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "type": m.type,
        "description": m.description,
        "body": m.body,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


@router.get("")
@tool(name="memory_list")
async def list_memories(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """查看当前用户的跨会话记忆列表（名称、类型、摘要、内容）。"""
    provider = get_provider()
    memories = await provider.list_all(db, user_id)
    return api_success([_to_dict(m) for m in memories])


@router.post("")
@tool(name="memory_add")
async def add_memory(
    data: MemoryCreate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """添加一条跨会话记忆。type 为 'user'（用户偏好/身份）或 'reference'（关注事项）。"""
    if data.type not in VALID_TYPES:
        return api_error(40011, f"type 必须是 {VALID_TYPES} 之一")
    provider = get_provider()
    try:
        mem = await provider.add(
            db, user_id,
            name=data.name.strip(),
            type=data.type,
            description=data.description.strip(),
            body=data.body.strip(),
        )
        await db.commit()
    except MemoryLimitError as e:
        await db.rollback()
        return api_error(40012, str(e))
    return api_success(_to_dict(mem))


@router.delete("/{memory_id}")
@tool(name="memory_delete")
async def delete_memory(
    memory_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """删除一条跨会话记忆（仅限本人）。"""
    provider = get_provider()
    deleted = await provider.delete(db, user_id, memory_id)
    await db.commit()
    return api_success({"deleted": deleted})

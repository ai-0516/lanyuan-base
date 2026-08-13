"""记忆管理 API（跨会话记忆 #9）

只负责路由与 @tool 定义（review #3 分层），实现全部在 memory_service：
- GET /memory        → 查看自己的记忆列表
- POST /memory       → 添加记忆
- DELETE /memory/{id} → 删除记忆

tool 场景与 REST 场景共用同一 service 实现：
tool_registry 注入 db/user_id 后直接调本文件函数 → 内部走 memory_service。
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.response import api_error, api_success
from app.harness.memory import VALID_TYPES, MemoryLimitError
from app.harness.tool_registry import tool
from app.services import memory_service

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


def _format_memory_list(data) -> str:
    """记忆列表 → LLM 摘要（id 即索引，#N 可被 memory_get 取全文）"""
    if not data:
        return "暂无记忆"
    lines = [f"共 {len(data)} 条记忆："]
    for m in data:
        lines.append(f"  #{m['id']} [{m['type']}] {m['name']}：{m['description']}")
    return "\n".join(lines)


def _format_memory(data) -> str:
    """单条记忆（get/add 共用）→ LLM 摘要"""
    if not data:
        return "操作未成功（记忆不存在或参数非法）"
    return (
        f"记忆 #{data['id']} [{data['type']}] {data['name']}\n"
        f"摘要：{data['description']}\n"
        f"内容：{data['body']}"
    )


def _format_memory_delete(data) -> str:
    """删除记忆结果 → LLM 摘要（幂等：不存在也视为成功）"""
    return "记忆已删除" if data.get("deleted") else "该记忆不存在（无需删除）"


@router.get("")
@tool(name="memory_list", result_formatter=_format_memory_list)
async def list_memories(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """查看当前用户的跨会话记忆列表（名称、类型、摘要、内容）。"""
    memories = await memory_service.list_memories(db, user_id)
    return api_success([_to_dict(m) for m in memories])


@router.post("")
@tool(name="memory_add", result_formatter=_format_memory)
async def add_memory(
    data: MemoryCreate,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """添加一条跨会话记忆。type 为 'user'（用户偏好/身份）或 'reference'（关注事项）。"""
    if data.type not in VALID_TYPES:
        return api_error(40011, f"type 必须是 {VALID_TYPES} 之一")
    try:
        mem = await memory_service.add_memory(
            db, user_id,
            name=data.name.strip(),
            type=data.type,
            description=data.description.strip(),
            body=data.body.strip(),
        )
        await db.commit()
        logger.info("记忆写入: user=%s id=%s name=%s type=%s",
                    user_id, mem.id, mem.name, mem.type)
    except MemoryLimitError as e:
        await db.rollback()
        logger.warning("记忆写入失败（超限）: user=%s name=%s err=%s",
                       user_id, data.name, e)
        return api_error(40012, str(e))
    return api_success(_to_dict(mem))


@router.get("/{memory_id}")
@tool(name="memory_get", result_formatter=_format_memory)
async def get_memory(
    memory_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """获取一条跨会话记忆的完整内容（按 id，仅限本人）。索引中形如 #12 的编号即 id。"""
    mem = await memory_service.get_memory(db, user_id, memory_id)
    if mem is None:
        return api_success(None)  # 查无此条 = 正常结果（业务失败≠系统异常）
    logger.info("记忆读取: user=%s id=%s name=%s", user_id, memory_id, mem.name)
    return api_success(_to_dict(mem))


@router.delete("/{memory_id}")
@tool(name="memory_delete", result_formatter=_format_memory_delete)
async def delete_memory(
    memory_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user),
):
    """删除一条跨会话记忆（仅限本人）。幂等：id 不存在也视为成功。"""
    deleted = await memory_service.delete_memory(db, user_id, memory_id)
    await db.commit()
    logger.info("记忆删除: user=%s id=%s deleted=%s", user_id, memory_id, deleted)
    return api_success({"deleted": deleted})

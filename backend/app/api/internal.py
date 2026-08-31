"""FastAPI 内部端点（TECH_SPEC §6.3 M3 身份查询插件：DSH 子进程专用通道）

- `GET /api/internal/sessions/{id}/owner` → {owner_user_id}；无映射 → 404
  （桥插件 fail-closed：查不到即拒绝工具执行）
- 鉴权：`X-Lanyuan-Internal-Token`（与 /mcp 同款内部共享密钥，§12.2）——
  唯一合法 client 是本进程 DSH 子进程（LANYUAN_MCP_TOKEN 由 dsh_runtime
  注入其 env）；外部 client 无法直连伪造身份。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.session_service import get_session_owner
from app.core.database import get_db
from app.core.security import verify_mcp_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["内部端点（DSH 子进程专用）"])


async def require_internal_token(
    x_lanyuan_internal_token: str | None = Header(default=None),
) -> None:
    """内部共享密钥校验（fail-closed；缺失/错误一律 401）。"""
    if not verify_mcp_token(x_lanyuan_internal_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 40110, "message": "未授权的内部请求"},
        )


@router.get("/sessions/{session_id}/owner")
async def session_owner(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_internal_token),
):
    """session_id → owner_user_id（桥插件 execute 时查询，§6.3）。"""
    owner = await get_session_owner(db, session_id)
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": 40410, "message": "会话不存在"},
        )
    return {"owner_user_id": owner}

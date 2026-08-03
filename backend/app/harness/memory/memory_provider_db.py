"""跨会话记忆 — 数据库版 provider（#9）

DBMemoryProvider 是第一个实现（数据存数据库，开发 SQLite / 生产 MySQL）。
只做纯存储操作（add/delete/list_all/search/get/count/replace_all）；
LLM 驱动的抽取（extract）/合并（consolidate）在 memory.py 编排层实现。
未来可加其他 provider（如调用云 API 做云端存储），每个 provider 独立文件
（review #6）：新增 provider 时复制本文件并实现 MemoryProvider 接口即可。
"""

import logging

from sqlalchemy import delete as _sql_delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from .memory_provider import (
    MEMORY_TYPE_USER,
    VALID_TYPES,
    MemoryProvider,
)
from ...models.user_memory import UserMemory

logger = logging.getLogger(__name__)


class DBMemoryProvider(MemoryProvider):
    """数据库版 provider（首个实现）

    存储介质为数据库（开发 SQLite / 生产 MySQL），不绑定具体 DBMS。
    不做 LLM 判断——抽取/合并在 memory.py 编排层。
    """

    name = "db"

    # ── 写入 ─────────────────────────────────────────

    async def add(
        self,
        db: AsyncSession,
        user_id: int,
        *,
        name: str,
        type: str,
        description: str,
        body: str,
    ) -> UserMemory:
        # 类型校验（非法类型回退 user）
        if type not in VALID_TYPES:
            type = MEMORY_TYPE_USER

        mem = UserMemory(
            user_id=user_id,
            name=name,
            type=type,
            description=description,
            body=body,
        )
        db.add(mem)
        await db.flush()
        return mem

    async def delete(self, db: AsyncSession, user_id: int, memory_id: int) -> bool:
        # 幂等：不抛异常即成功（id 不存在/非本人目标状态已达成），
        # 返回 bool 仅表示是否实际删除（review #7/#8/#12）
        result = await db.execute(
            _sql_delete(UserMemory).where(
                UserMemory.id == memory_id,
                UserMemory.user_id == user_id,
            )
        )
        return result.rowcount > 0

    async def replace_all(
        self,
        db: AsyncSession,
        user_id: int,
        items: list[dict],
    ) -> int:
        """全删重写（consolidate 合并结果落库）。原子性由调用方事务保证。"""
        await db.execute(_sql_delete(UserMemory).where(UserMemory.user_id == user_id))
        count = 0
        for item in items:
            db.add(UserMemory(
                user_id=user_id,
                name=item["name"],
                type=item["type"],
                description=item["description"],
                body=item["body"],
            ))
            count += 1
        return count

    # ── 读取 ─────────────────────────────────────────

    async def list_all(self, db: AsyncSession, user_id: int) -> list[UserMemory]:
        stmt = (
            select(UserMemory)
            .where(UserMemory.user_id == user_id)
            .order_by(UserMemory.updated_at.desc(), UserMemory.id.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def search(
        self,
        db: AsyncSession,
        user_id: int,
        keywords: list[str],
        limit: int = 5,
    ) -> list[UserMemory]:
        if not keywords:
            return []
        stmt = (
            select(UserMemory)
            .where(UserMemory.user_id == user_id)
            .order_by(UserMemory.updated_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        memories = list(result.scalars().all())

        # 关键词打分：body 命中 2 分，name/description 命中 1 分
        scored: list[tuple[int, UserMemory]] = []
        for m in memories:
            score = 0
            for kw in keywords:
                kwl = kw.lower()
                if kwl in (m.body or "").lower():
                    score += 2
                if kwl in (m.name or "").lower() or kwl in (m.description or "").lower():
                    score += 1
            if score > 0:
                scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:limit]]

    async def get(self, db: AsyncSession, user_id: int, memory_id: int) -> UserMemory | None:
        """按 id 获取单条记忆（仅限本人）。不存在返回 None。"""
        stmt = select(UserMemory).where(
            UserMemory.id == memory_id,
            UserMemory.user_id == user_id,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def count(self, db: AsyncSession, user_id: int) -> int:
        result = await db.execute(
            select(func.count()).select_from(UserMemory).where(UserMemory.user_id == user_id)
        )
        return int(result.scalar() or 0)

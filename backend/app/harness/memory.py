"""跨会话记忆 — provider 抽象层（#9）

MemoryProvider 定义三个基本操作：
- 写入: add / delete
- 读取: list（索引）/ search（按需召回）
- 抽取: extract（从对话中提取值得记住的信息）

MySQLMemoryProvider 是第一个实现（数据存数据库）。
未来可加其他 provider（如调用云 API 做云端存储/读取/抽取）。

设计原则：
- provider 只做存储/读取/抽取，不关心会话、SSE、事件
- 用户隔离：所有操作按 user_id 过滤，用户 A 看不到用户 B 的记忆
- 上限：每用户 MEMORY_MAX_PER_USER 条，写入超限时先触发 LLM 合并再写入
- 时间戳：created_at / updated_at 供合并时判断新旧覆盖
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user_memory import UserMemory

logger = logging.getLogger(__name__)

# 记忆类型（社区助手场景只需两类）
MEMORY_TYPE_USER = "user"          # 用户偏好、身份（"我叫张三"、"喜欢简洁回复"）
MEMORY_TYPE_REFERENCE = "reference"  # 关注点、线索（"3号楼漏水问题"、"暖气费"）

VALID_TYPES = (MEMORY_TYPE_USER, MEMORY_TYPE_REFERENCE)

# 上限常量（默认值，可被 config 覆盖）
MAX_PER_USER = settings.MEMORY_MAX_PER_USER


class MemoryLimitError(Exception):
    """记忆条数超过上限"""


class MemoryProvider(ABC):
    """记忆 provider 抽象层"""

    name: str = "base"

    @abstractmethod
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
        """写入一条记忆。

        若该用户记忆条数已达上限，先触发 LLM 合并（consolidate）腾出空间，
        合并后仍无法写入则抛 MemoryLimitError。
        """

    @abstractmethod
    async def delete(self, db: AsyncSession, user_id: int, memory_id: int) -> bool:
        """删除一条记忆（仅限本人）。返回是否删除成功。"""

    @abstractmethod
    async def list_all(self, db: AsyncSession, user_id: int) -> list[UserMemory]:
        """读取该用户全部记忆（按 updated_at 倒序，供索引注入）。"""

    @abstractmethod
    async def search(
        self,
        db: AsyncSession,
        user_id: int,
        keywords: list[str],
        limit: int = 5,
    ) -> list[UserMemory]:
        """关键词召回：匹配 name / description / body，返回完整记忆。"""

    @abstractmethod
    async def extract(
        self,
        db: AsyncSession,
        user_id: int,
        messages: list[dict],
    ) -> int:
        """从对话消息中抽取值得记住的新记忆并写入，返回新增条数。"""

    @abstractmethod
    async def consolidate(self, db: AsyncSession, user_id: int) -> int:
        """合并去重该用户全部记忆（LLM 低频操作），返回合并后的条数。"""


class MySQLMemoryProvider(MemoryProvider):
    """数据库版 provider（首个实现）

    抽取和合并依赖 LLM（复用 streaming.deepseek_chat），
    存储介质为 MySQL（开发环境 SQLite）。
    """

    name = "mysql"

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
        # 类型校验
        if type not in VALID_TYPES:
            type = MEMORY_TYPE_USER

        # 超限 → 先合并腾空间（低频，只有满了才触发）
        count = await self._count(db, user_id)
        if count >= MAX_PER_USER:
            before = count
            await self.consolidate(db, user_id)
            count = await self._count(db, user_id)
            if count >= MAX_PER_USER:
                logger.warning("记忆合并后仍超限: user_id=%s before=%s after=%s", user_id, before, count)
                raise MemoryLimitError(f"记忆已满（上限 {MAX_PER_USER} 条），请先清理")

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
        result = await db.execute(
            delete(UserMemory).where(
                UserMemory.id == memory_id,
                UserMemory.user_id == user_id,
            )
        )
        return result.rowcount > 0

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

    # ── 抽取 / 合并 ──────────────────────────────────

    async def extract(
        self,
        db: AsyncSession,
        user_id: int,
        messages: list[dict],
    ) -> int:
        """从最近对话中抽取记忆。

        messages 为 OpenAI 格式消息（role/content），只取最近的 user 消息做判断，
        不把 assistant 工具调用噪音喂给 LLM。
        """
        if not messages:
            return 0

        # 已有记忆，用于去重
        existing = await self.list_all(db, user_id)
        existing_lines = "\n".join(
            f"- [{m.type}] {m.name}: {m.description}" for m in existing
        ) if existing else "(无)"

        # 取最近若干条 user 消息
        user_texts = [m.get("content", "") for m in messages if m.get("role") == "user"]
        user_texts = [t for t in user_texts if isinstance(t, str) and t.strip()][-5:]
        if not user_texts:
            return 0
        dialogue = "\n".join(f"用户: {t[:500]}" for t in user_texts)

        prompt = (
            "你是记忆抽取器。根据下面这段用户对话，判断是否有值得跨会话记住的信息。\n"
            "规则：\n"
            "1. 只抽取『用户偏好、身份信息、关注事项』这类跨会话仍有用的事实\n"
            "2. 用户显式说『记住』或表达稳定偏好时必须抽取\n"
            "3. 一次性的临时请求（如『帮我查下暖气费』）不抽取\n"
            "4. 与已有记忆重复的内容不抽取\n"
            "输出 JSON 数组，每项: {\"name\": \"短标识kebab-case\", \"type\": \"user|reference\", "
            "\"description\": \"一句话摘要\", \"body\": \"完整内容\"}。\n"
            "没有值得记住的内容时输出 []。\n\n"
            f"已有记忆：\n{existing_lines}\n\n"
            f"对话：\n{dialogue}"
        )

        text = await self._call_llm(prompt)
        items = self._parse_json_array(text)
        if not items:
            return 0

        added = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            desc = str(item.get("description", "")).strip()
            body = str(item.get("body", "")).strip()
            if not name or not desc or not body:
                continue
            # 去重：同 name 已存在则更新（覆盖旧记忆，如搬家场景）
            try:
                await self.add(
                    db, user_id,
                    name=name[:100],
                    type=str(item.get("type", "user")),
                    description=desc[:255],
                    body=body,
                )
                added += 1
            except MemoryLimitError:
                logger.warning("记忆超限，抽取跳过: user_id=%s name=%s", user_id, name)
        return added

    async def consolidate(self, db: AsyncSession, user_id: int) -> int:
        """LLM 合并去重：把该用户全部记忆发给 LLM，去重/合并/删过时，保留重要项。"""
        memories = await self.list_all(db, user_id)
        if len(memories) < 2:
            return len(memories)

        catalog_lines = []
        for m in memories:
            catalog_lines.append(
                f"[{m.id}] [{m.type}] {m.name} ({m.created_at:%Y-%m-%d}):\n"
                f"  {m.description}\n  {m.body}"
            )
        catalog = "\n\n".join(catalog_lines)

        prompt = (
            "你是记忆整理器。下面是某用户已保存的全部记忆，请合并去重：\n"
            "1. 内容重复或高度重叠的合并为一条\n"
            "2. 已被新信息覆盖的旧记忆删除（如用户从北京搬到上海，旧地址作废）\n"
            "3. 保留重要的用户偏好和关注事项\n"
            "4. 总条数控制在 20 条以内，每条更精炼\n"
            "输出 JSON 数组，每项: {\"name\": \"短标识kebab-case\", \"type\": \"user|reference\", "
            "\"description\": \"一句话摘要\", \"body\": \"完整内容\"}。\n\n"
            f"记忆清单：\n{catalog}"
        )

        text = await self._call_llm(prompt)
        items = self._parse_json_array(text)
        if not items:
            logger.warning("合并返回空，保持原样: user_id=%s", user_id)
            return len(memories)

        # 全删重写
        await db.execute(delete(UserMemory).where(UserMemory.user_id == user_id))
        count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            desc = str(item.get("description", "")).strip()
            body = str(item.get("body", "")).strip()
            if not name or not desc or not body:
                continue
            db.add(UserMemory(
                user_id=user_id,
                name=name[:100],
                type=str(item.get("type", "user")),
                description=desc[:255],
                body=body,
            ))
            count += 1
        logger.info("记忆合并完成: user_id=%s %s→%s 条", user_id, len(memories), count)
        return count

    # ── 内部工具 ─────────────────────────────────────

    async def _count(self, db: AsyncSession, user_id: int) -> int:
        result = await db.execute(
            select(func.count()).select_from(UserMemory).where(UserMemory.user_id == user_id)
        )
        return int(result.scalar() or 0)

    async def _call_llm(self, prompt: str) -> str:
        """单次 LLM 调用（TEXT ONLY），复用 streaming.deepseek_chat"""
        from app.harness import streaming  # 函数内 import，避免模块循环依赖

        parts: list[str] = []
        async for event, data in streaming.deepseek_chat(
            [{"role": "user", "content": prompt}]
        ):
            if event == "token":
                assert isinstance(data, str)
                parts.append(data)
            elif event == "error":
                raise RuntimeError(f"记忆 LLM 调用失败: {data}")
        return "".join(parts).strip()

    @staticmethod
    def _parse_json_array(text: str) -> list[Any]:
        """从 LLM 输出中解析 JSON 数组（容忍 markdown 代码块包裹）"""
        if not text:
            return []
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        import re
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            logger.warning("记忆 LLM 输出解析失败: %s", text[:200])
            return []


# 全局 provider 实例（当前只有一个 DB provider）
_provider: MemoryProvider | None = None


def get_provider() -> MemoryProvider:
    """获取当前激活的 memory provider（默认 MySQL）"""
    global _provider
    if _provider is None:
        _provider = MySQLMemoryProvider()
    return _provider

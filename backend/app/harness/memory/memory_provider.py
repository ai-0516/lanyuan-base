"""跨会话记忆 — provider 抽象层（#9）

MemoryProvider 定义三个基本操作：
- 写入: add / delete
- 读取: list（索引）/ search（按需召回）
- 抽取: extract（从对话中提取值得记住的信息）

设计原则：
- provider 只做存储/读取/抽取，不关心会话、SSE、事件
- 用户隔离：所有操作按 user_id 过滤，用户 A 看不到用户 B 的记忆
- 上限：每用户 MEMORY_MAX_PER_USER 条，写入超限时先触发 LLM 合并再写入
- 时间戳：created_at / updated_at 供合并时判断新旧覆盖
"""

from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from ...models.user_memory import UserMemory

# 记忆类型（社区助手场景只需两类）
MEMORY_TYPE_USER = "user"          # 用户偏好、身份（"我叫张三"、"喜欢简洁回复"）
MEMORY_TYPE_REFERENCE = "reference"  # 关注点、线索（"3号楼漏水问题"、"暖气费"）

VALID_TYPES = (MEMORY_TYPE_USER, MEMORY_TYPE_REFERENCE)

# 上限常量（默认值，可被 config 覆盖）
MAX_PER_USER = settings.MEMORY_MAX_PER_USER

# 单条 body 长度上限（防止 LLM 写入超长内容，撑爆后续 consolidate 的 prompt）
BODY_MAX_LEN = 2000


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
        """删除一条记忆（仅限本人）。

        幂等语义：不抛异常即成功——id 不存在（或非本人）目标状态已达成，
        返回 bool 仅表示是否实际删除（供前端/LLM 可选展示），不算失败。
        """

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


def _parse_json_array(text: str) -> list[Any]:
    """从 LLM 输出中解析 JSON 数组（容忍 markdown 代码块包裹）

    放在 provider 模块供各 provider 复用（抽取/合并的输出解析）。
    """
    import json
    import logging

    logger = logging.getLogger(__name__)

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

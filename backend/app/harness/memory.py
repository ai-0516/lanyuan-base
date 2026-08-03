"""跨会话记忆 harness（#9）— provider 抽象 + 模块级对外接口

对外提供模块级函数（add/delete/list_all/search/extract/consolidate/select_relevant），
调用方不接触 MemoryProvider 类型与 get_provider——具体 provider 是内部实现细节。

设计原则：
- provider 只做存储/读取/抽取，不关心会话、SSE、事件
- 用户隔离：所有操作按 user_id 过滤，用户 A 看不到用户 B 的记忆
- 上限：每用户 MEMORY_MAX_PER_USER 条，写入超限时先触发 LLM 合并再写入
- 时间戳：created_at / updated_at 供合并时判断新旧覆盖
- 相关记忆选择（方案 b，2026-08-02 用户拍板）：关键词优先（零 LLM 调用）、
  LLM 兜底（语义召回）——成本在其次，首 token 延时优先
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import delete as _sql_delete, select, func
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

# 单条 body 长度上限（防止 LLM 写入超长内容，撑爆后续 consolidate 的 prompt）
BODY_MAX_LEN = 2000

# 相关记忆选择：最多注入几条完整记忆
_MAX_SELECT = 5


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


class DBMemoryProvider(MemoryProvider):
    """数据库版 provider（首个实现）

    抽取/合并/选择依赖 LLM（复用 streaming.deepseek_chat），
    存储介质为数据库（开发 SQLite / 生产 MySQL），不绑定具体 DBMS。
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
            _sql_delete(UserMemory).where(
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
                    body=body[:BODY_MAX_LEN],
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

        # LLM 失败降级：合并是「满了才触发」的辅助操作，失败保持原样即可，
        # 不能把错误抛给调用方（否则 add 超限路径直接 500，见 review #11）
        try:
            text = await self._call_llm(prompt)
        except Exception:
            logger.warning("合并 LLM 调用失败，保持原样: user_id=%s", user_id)
            return len(memories)
        items = self._parse_json_array(text)
        if not items:
            logger.warning("合并返回空，保持原样: user_id=%s", user_id)
            return len(memories)

        # 预解析：先过滤非法项，避免「非空但全部无效」时全删重写丢数据
        valid: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            desc = str(item.get("description", "")).strip()
            body = str(item.get("body", "")).strip()
            if not name or not desc or not body:
                continue
            valid.append({
                "name": name[:100],
                "type": str(item.get("type", "user")),
                "description": desc[:255],
                "body": body[:BODY_MAX_LEN],
            })
        if not valid:
            logger.warning("合并结果全部无效，保持原样: user_id=%s", user_id)
            return len(memories)

        # 全删重写
        await db.execute(_sql_delete(UserMemory).where(UserMemory.user_id == user_id))
        count = 0
        for item in valid:
            db.add(UserMemory(
                user_id=user_id,
                name=item["name"],
                type=item["type"],
                description=item["description"],
                body=item["body"],
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
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            logger.warning("记忆 LLM 输出解析失败: %s", text[:200])
            return []


# ═══════════════════════════════════════════
#  对外模块级接口（代理给 provider，provider 是内部细节）
# ═══════════════════════════════════════════

_provider: MemoryProvider | None = None


def _get_provider() -> MemoryProvider:
    """获取当前激活的 memory provider（内部使用，外部一律走模块级函数）"""
    global _provider
    if _provider is None:
        _provider = DBMemoryProvider()
    return _provider


async def add(
    db: AsyncSession,
    user_id: int,
    *,
    name: str,
    type: str,
    description: str,
    body: str,
) -> UserMemory:
    """写入一条记忆（超限先合并，仍满抛 MemoryLimitError）。"""
    return await _get_provider().add(
        db, user_id, name=name, type=type, description=description, body=body,
    )


async def delete(db: AsyncSession, user_id: int, memory_id: int) -> bool:
    """删除一条记忆（仅限本人）。幂等：id 不存在也视为成功，返回是否实际删除。"""
    return await _get_provider().delete(db, user_id, memory_id)


async def list_all(db: AsyncSession, user_id: int) -> list[UserMemory]:
    """读取该用户全部记忆（按 updated_at 倒序，供索引注入）。"""
    return await _get_provider().list_all(db, user_id)


async def search(
    db: AsyncSession,
    user_id: int,
    keywords: list[str],
    limit: int = 5,
) -> list[UserMemory]:
    """关键词召回：匹配 name / description / body，返回完整记忆。"""
    return await _get_provider().search(db, user_id, keywords, limit=limit)


async def extract(db: AsyncSession, user_id: int, messages: list[dict]) -> int:
    """从对话消息中抽取值得记住的新记忆并写入，返回新增条数。"""
    return await _get_provider().extract(db, user_id, messages)


async def consolidate(db: AsyncSession, user_id: int) -> int:
    """合并去重该用户全部记忆（LLM 低频操作），返回合并后的条数。"""
    return await _get_provider().consolidate(db, user_id)


# ═══════════════════════════════════════════
#  相关记忆选择（方案 b：关键词优先 + LLM 兜底）
# ═══════════════════════════════════════════

async def select_relevant(
    db: AsyncSession,
    user_id: int,
    user_message: str,
) -> list[UserMemory]:
    """返回与当前用户消息相关的完整记忆列表（最多 _MAX_SELECT 条）。

    关键词优先：有命中直接返回（零 LLM 调用）；无命中才走 LLM 兜底。
    关键词无命中时 LLM 失败不影响主流程，返回空。
    """
    if not user_message or not user_message.strip():
        return []

    # 1. 拿索引（name + description）
    memories = await list_all(db, user_id)
    if not memories:
        return []

    # 2. 关键词优先（零 LLM 调用，首 token 延时最低）
    keywords = _extract_keywords(user_message)
    if keywords:
        hits = await search(db, user_id, keywords, limit=_MAX_SELECT)
        if hits:
            return hits[:_MAX_SELECT]

    # 3. LLM 兜底（关键词无命中 → 语义召回；失败返回 None → 空）
    selected = await _llm_select(user_message, memories)
    if selected is not None:
        return selected[:_MAX_SELECT]
    return []


async def _llm_select(user_message: str, memories: list) -> list | None:
    """LLM side-query：从索引中选相关记忆。失败返回 None（触发降级）。"""
    catalog_lines = []
    for i, m in enumerate(memories):
        catalog_lines.append(f"{i}: [{m.type}] {m.name} — {m.description}")
    catalog = "\n".join(catalog_lines)

    prompt = (
        "根据用户当前消息，从下面的记忆索引中选出相关的记忆编号。\n"
        "规则：只选明显相关的；没有相关就返回空数组 []；最多选 5 个。\n"
        "只输出 JSON 数组，如 [0, 3]。\n\n"
        f"记忆索引：\n{catalog}\n\n"
        f"用户消息：{user_message[:1000]}"
    )

    try:
        from app.harness import streaming  # 函数内 import，避免循环依赖

        parts: list[str] = []
        async for event, data in streaming.deepseek_chat(
            [{"role": "user", "content": prompt}]
        ):
            if event == "token":
                assert isinstance(data, str)
                parts.append(data)
            elif event == "error":
                raise RuntimeError(f"记忆选择调用失败: {data}")

        text = "".join(parts).strip()
        match = re.search(r"\[.*?\]", text, re.DOTALL)
        if not match:
            return []
        indices = json.loads(match.group())
        selected = []
        for idx in indices:
            if isinstance(idx, int) and 0 <= idx < len(memories):
                selected.append(memories[idx])
        return selected
    except Exception:
        logger.exception("LLM 选相关记忆失败，降级关键词")
        return None


def _extract_keywords(user_message: str) -> list[str]:
    """提取关键词：英文单词 + 中文 2 字滑窗。

    2 字是中文最小语义单元（火锅/爬山/喜欢），子串命中记忆的 body/name 概率
    远高于整段贪婪截断；噪声词（如「我喜」）在 search 打分里 score=0 被过滤。
    """
    words = re.findall(r"[a-zA-Z]{2,}", user_message)
    cn_segments = re.findall(r"[\u4e00-\u9fff]+", user_message)
    bigrams = []
    for seg in cn_segments:
        bigrams.extend(seg[i : i + 2] for i in range(len(seg) - 1))
    keywords = list(dict.fromkeys([w.lower() for w in words] + bigrams))
    return keywords[:20]

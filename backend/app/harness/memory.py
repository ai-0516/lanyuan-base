"""跨会话记忆 harness（#9）— 统一对外接口

对外提供模块级函数（add/delete/list_all/search/extract/consolidate/select_relevant），
调用方不接触 MemoryProvider 类型——具体 provider 是内部实现细节（review #13）。

文件结构（review #5/#6）：
- memory_provider.py：MemoryProvider 抽象层 + 常量
- memory_db.py：DBMemoryProvider（数据库版实现，每 provider 独立文件）
- memory.py：模块级接口 + 相关记忆选择 + 记忆文本格式化（#9 归入 memory harness）

相关记忆选择（方案 b，2026-08-02 用户拍板）：关键词优先（零 LLM 调用）、
LLM 兜底（语义召回）——成本在其次，首 token 延时优先。
"""

import json
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.harness.memory_db import DBMemoryProvider
from app.harness.memory_provider import (
    MemoryProvider,
)
from app.models.user_memory import UserMemory

logger = logging.getLogger(__name__)

# 相关记忆选择：最多注入几条完整记忆
_MAX_SELECT = 5


# ═══════════════════════════════════════════
#  模块级接口（代理给 provider，provider 是内部细节）
# ═══════════════════════════════════════════

# review #8：直接初始化 provider，不经过 _get_provider 函数
_provider: MemoryProvider = DBMemoryProvider()


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
    return await _provider.add(
        db, user_id, name=name, type=type, description=description, body=body,
    )


async def delete(db: AsyncSession, user_id: int, memory_id: int) -> bool:
    """删除一条记忆（仅限本人）。幂等：id 不存在也视为成功，返回是否实际删除。"""
    return await _provider.delete(db, user_id, memory_id)


async def list_all(db: AsyncSession, user_id: int) -> list[UserMemory]:
    """读取该用户全部记忆（按 updated_at 倒序，供索引注入）。"""
    return await _provider.list_all(db, user_id)


async def search(
    db: AsyncSession,
    user_id: int,
    keywords: list[str],
    limit: int = 5,
) -> list[UserMemory]:
    """关键词召回：匹配 name / description / body，返回完整记忆。"""
    return await _provider.search(db, user_id, keywords, limit=limit)


async def extract(db: AsyncSession, user_id: int, messages: list[dict]) -> int:
    """从对话消息中抽取值得记住的新记忆并写入，返回新增条数。"""
    return await _provider.extract(db, user_id, messages)


async def consolidate(db: AsyncSession, user_id: int) -> int:
    """合并去重该用户全部记忆（LLM 低频操作），返回合并后的条数。"""
    return await _provider.consolidate(db, user_id)


# ═══════════════════════════════════════════
#  记忆文本格式化（#9：build_memory_index/relevant 归入 memory harness）
# ═══════════════════════════════════════════

def build_memory_description(memories: list) -> str:
    """生成记忆索引文本（常驻 SYSTEM PROMPT，review #5 改名）

    每行一条：类型 + 一句话描述（name 是 kebab-case 短标识，对 LLM 无语义增益，去掉）。
    缓存设计（review #7）：记忆不变则文本不变，作为 system 稳定前缀的一部分，天然可缓存。
    """
    if not memories:
        return ""
    lines = []
    for m in memories[: settings.MEMORY_INDEX_LIMIT]:
        lines.append(f"- [{m.type}] {m.description}")
    return "你的记忆索引：\n" + "\n".join(lines)


def build_memory_body(memories: list) -> str:
    """生成相关记忆完整内容（拼进 SYSTEM PROMPT，review #6 改名）

    每段：类型 + body（body 已是完整内容，name 无语义增益）。
    """
    if not memories:
        return ""
    parts = []
    for m in memories:
        parts.append(f"[{m.type}] {m.body}")
    return "与当前对话相关的记忆：\n" + "\n\n".join(parts)


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

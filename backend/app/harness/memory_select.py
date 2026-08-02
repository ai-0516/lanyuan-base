"""相关记忆选择 — 新会话/每轮开始时按需加载

用户消息 → LLM 选相关（side-query，基于记忆索引 name+description）
→ 加载选中的完整记忆注入上下文。LLM 失败时降级为关键词匹配。

设计：
- 只在用户消息非空且记忆非空时调用（省调用）
- LLM 选择失败不影响主流程：降级关键词，关键词也无命中则返回空
- 与 provider.search 的关系：search 是 provider 的关键词能力，
  本模块是 harness 层的智能选择（LLM 优先 + 关键词兜底）
"""

import json
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.harness.memory import MemoryProvider

logger = logging.getLogger(__name__)

_MAX_SELECT = 5  # 最多注入几条完整记忆


async def select_relevant(
    db: AsyncSession,
    user_id: int,
    user_message: str,
    provider: MemoryProvider,
) -> list:
    """返回与当前用户消息相关的完整记忆列表（最多 _MAX_SELECT 条）"""
    if not user_message or not user_message.strip():
        return []

    # 1. 拿索引（name + description）
    memories = await provider.list_all(db, user_id)
    if not memories:
        return []

    # 2. LLM 选相关
    selected = await _llm_select(user_message, memories)
    if selected is not None:
        return selected[:_MAX_SELECT]

    # 3. 降级：关键词匹配
    keywords = _extract_keywords(user_message)
    if not keywords:
        return []
    return await provider.search(db, user_id, keywords, limit=_MAX_SELECT)


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
    """提取中文关键词（2~6 字的连续片段）+ 英文单词"""
    words = re.findall(r"[a-zA-Z]{2,}", user_message)
    cn_parts = re.findall(r"[\u4e00-\u9fff]{2,6}", user_message)
    keywords = list(dict.fromkeys([w.lower() for w in words] + cn_parts))
    return keywords[:10]

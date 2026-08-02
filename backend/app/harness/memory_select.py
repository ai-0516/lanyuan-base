"""相关记忆选择 — 新会话/每轮开始时按需加载

用户消息 → 关键词优先（零 LLM 调用，首 token 延时最低）
→ 关键词无命中时 LLM 兜底（side-query，基于记忆索引 name+description）
→ 加载选中的完整记忆注入上下文。

方案 b（2026-08-02 用户拍板）：关键词优先、LLM 兜底——成本在其次，
首 token 延时优先。日常消息关键词命中即返回，不调 LLM；
只有语义相关但无共同关键词（如「周末去哪玩」vs 记忆「爱好爬山」）才付 LLM 成本。

设计：
- 只在用户消息非空且记忆非空时调用（省调用）
- 关键词命中 → 直接用（最多 _MAX_SELECT 条），LLM 完全不参与
- 关键词无命中 → LLM 选相关；LLM 失败不影响主流程，返回空
- 与 provider.search 的关系：search 是 provider 的关键词打分能力，
  本模块是 harness 层的顺序策略（关键词优先 + LLM 兜底）
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
    """返回与当前用户消息相关的完整记忆列表（最多 _MAX_SELECT 条）。

    关键词优先：有命中直接返回（零 LLM 调用）；无命中才走 LLM 兜底。
    """
    if not user_message or not user_message.strip():
        return []

    # 1. 拿索引（name + description）
    memories = await provider.list_all(db, user_id)
    if not memories:
        return []

    # 2. 关键词优先（零 LLM 调用，首 token 延时最低）
    keywords = _extract_keywords(user_message)
    if keywords:
        hits = await provider.search(db, user_id, keywords, limit=_MAX_SELECT)
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

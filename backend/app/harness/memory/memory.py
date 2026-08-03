"""跨会话记忆 harness（#9）— 统一对外接口

对外提供模块级函数（add/delete/list_all/search/get/extract/consolidate/select_relevant），
调用方不接触 MemoryProvider 类型——具体 provider 是内部实现细节（review #13）。

分层（2026-08-03 用户讨论确定）：
- memory_provider.py：MemoryProvider 抽象（纯存储接口 add/delete/get/list_all/search/
  count/replace_all）+ 常量
- memory_provider_db.py：DBMemoryProvider（数据库版实现，每 provider 独立文件）
- memory.py：模块级接口 + 编排层——**LLM 动作在 harness 层**：
  extract（抽取）/ consolidate（合并）实现在本文件，provider 只负责保存；
  add 的超限合并编排也在本层

相关记忆选择（方案 b，2026-08-02 用户拍板）：关键词优先（零 LLM 调用）、
LLM 兜底（语义召回）——成本在其次，首 token 延时优先。
"""

import json
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from .memory_provider import (
    BODY_MAX_LEN,
    MAX_PER_USER,
    MemoryLimitError,
    MemoryProvider,
    _parse_json_array,
)
from .memory_provider_db import DBMemoryProvider
from ...models.user_memory import UserMemory

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
    """写入一条记忆（2026-08-03：超限合并编排在 harness 层，provider 只做纯插入）。

    超限 → 先 LLM 合并腾空间，仍满抛 MemoryLimitError。
    """
    # 超限 → 先合并腾空间（低频，只有满了才触发）
    count = await _provider.count(db, user_id)
    if count >= MAX_PER_USER:
        before = count
        await consolidate(db, user_id)
        count = await _provider.count(db, user_id)
        if count >= MAX_PER_USER:
            logger.warning("记忆合并后仍超限: user_id=%s before=%s after=%s",
                           user_id, before, count)
            raise MemoryLimitError(f"记忆已满（上限 {MAX_PER_USER} 条），请先清理")

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


async def get(db: AsyncSession, user_id: int, memory_id: int) -> UserMemory | None:
    """按 id 获取单条记忆（仅限本人）。不存在返回 None。"""
    return await _provider.get(db, user_id, memory_id)


async def extract(db: AsyncSession, user_id: int, messages: list[dict]) -> int:
    """从对话消息中抽取值得记住的新记忆并写入，返回新增条数。

    2026-08-03（用户讨论确定）：抽取是 LLM 动作，实现在 harness 编排层
    （provider 只负责保存）。messages 为 OpenAI 格式消息（role/content），
    只取最近的 user 消息做判断，不把 assistant 工具调用噪音喂给 LLM。
    """
    if not messages:
        return 0

    # 已有记忆，用于去重
    existing = await list_all(db, user_id)
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

    text = await _call_llm(prompt)
    items = _parse_json_array(text)
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
        # 调用本模块的 add：超限判断/合并编排已在 add 内（超限→合并→仍满抛
        # MemoryLimitError），extract 捕获后跳过该条即可，无需重复 count
        try:
            await add(
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


async def consolidate(db: AsyncSession, user_id: int) -> int:
    """LLM 合并去重：把该用户全部记忆发给 LLM，去重/合并/删过时，保留重要项。

    2026-08-03（用户讨论确定）：合并是 LLM 动作，实现在 harness 编排层，
    落库走 provider.replace_all（全删重写，原子性由调用方事务保证）。
    """
    memories = await list_all(db, user_id)
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
        text = await _call_llm(prompt)
    except Exception:
        logger.warning("合并 LLM 调用失败，保持原样: user_id=%s", user_id)
        return len(memories)
    items = _parse_json_array(text)
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

    # 全删重写（provider 层操作）
    count = await _provider.replace_all(db, user_id, valid)
    logger.info("记忆合并完成: user_id=%s %s→%s 条", user_id, len(memories), count)
    return count


async def _call_llm(prompt: str) -> str:
    """单次 LLM 调用（TEXT ONLY），复用 streaming.deepseek_chat

    2026-08-03：抽取/合并共用（provider 不再持有 LLM 能力）。
    """
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


# ═══════════════════════════════════════════
#  记忆文本格式化（#9：build_memory_index/relevant 归入 memory harness）
# ═══════════════════════════════════════════

async def build_memory_index(db: AsyncSession, user_id: int) -> str:
    """构建记忆索引文本（SYSTEM PROMPT 用）：取全部记忆并格式化为索引。

    组合接口：调用方无需先 list_all 再 build_memory_description。
    """
    memories = await list_all(db, user_id)
    return build_memory_description(memories)


def build_memory_description(memories: list) -> str:
    """生成记忆索引文本（常驻 SYSTEM PROMPT，review #5 改名）

    每行一条：类型 + 编号（#id，供 LLM 调 memory_get 定位单条）+ 一句话描述。
    name 是 kebab-case 短标识，对 LLM 无语义增益，去掉。
    缓存设计（review #7）：记忆不变则文本不变，作为 system 稳定前缀的一部分，天然可缓存。
    """
    if not memories:
        return ""
    lines = []
    for m in memories[: settings.MEMORY_INDEX_LIMIT]:
        lines.append(f"- [{m.type}] #{m.id} {m.description}")
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

"""
结果清理钩子 — 移除 LLM 不关心的 base64 图片数据并截断过长的结果

替代 ToolDef.execute() 中的 _strip_base64_uris 和 truncation 逻辑。
"""

import re

from app.harness.hooks.events import on

_BASE64_PATTERN = re.compile(r'"data:image/[^;]+;base64,[A-Za-z0-9+/=]{100,}"')
_MAX_RESULT_LENGTH = 50000


@on("tool:end")
async def sanitize_tool_result(tool_name: str, result: str, **_kwargs) -> str | None:
    """清洗工具结果：移除 base64 头像数据并截断

    只在实际有改动时返回清洗后的字符串，否则返回 None。
    """
    cleaned = _BASE64_PATTERN.sub('""', result)
    if len(cleaned) > _MAX_RESULT_LENGTH:
        cleaned = cleaned[:_MAX_RESULT_LENGTH] + "…(结果过长已截断)"
    if cleaned == result:
        return None
    return cleaned

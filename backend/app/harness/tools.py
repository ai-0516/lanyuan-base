"""工具定义与执行

每个工具 = 一个 JSON schema 定义 + 一个执行函数。
"""

import json
import logging

from app.schemas.post import PostCreate
from app.services import post_service

logger = logging.getLogger(__name__)

# ── 工具定义 ──────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_post",
            "description": "发布一条帖子到社区。用户在这里分享生活、寻求帮助或组织活动。支持图文混排，最多9张图片。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "帖子正文内容",
                    },
                    "images": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "图片 URL 列表（可选，最多 9 张）",
                    },
                },
                "required": ["content"],
            },
        },
    },
]


# ── 工具执行 ──────────────────────────────────────


async def execute_tool(db, user_id: int, tool_call: dict) -> str:
    """执行一个工具调用，返回结果字符串

    tool_call 格式（OpenAI API 兼容）：
      {
        "id": "call_xxx",
        "type": "function",
        "function": {
          "name": "create_post",
          "arguments": "{\"content\": \"...\"}"
        }
      }
    """
    name = tool_call.get("function", {}).get("name", "")
    raw_args = tool_call.get("function", {}).get("arguments", "{}")

    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError:
        return f"参数解析失败: {raw_args}"

    if name == "create_post":
        logger.info("Tool: create_post args=%s", raw_args)
        return await _handle_create_post(db, user_id, args)

    return f"未知工具: {name}"


async def _handle_create_post(db, user_id: int, args: dict) -> str:
    """处理 create_post 工具调用"""
    content = args.get("content", "")
    images = args.get("images", [])

    if not content.strip():
        return "帖子内容不能为空"

    data = PostCreate(content=content, images=images)
    result = await post_service.create_post(db, user_id, data)
    await db.commit()

    return json.dumps({
        "success": True,
        "post_id": result.id,
        "message": f"帖子发布成功（id={result.id}）",
    }, ensure_ascii=False)

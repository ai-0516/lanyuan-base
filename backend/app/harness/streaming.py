"""DeepSeek API 流式客户端 + 模拟回复

职责：
- 模拟回复（无 API Key 时的 fallback）
- DeepSeek API 的 HTTP SSE 请求
- 逐 token 产出 (event, data) 元组
"""

import json

import httpx

from app.config import settings

MOCK_REPLY_TEMPLATE = (
    "收到您的消息：「{message}」\n\n"
    "（当前为模拟模式，未配置 DeepSeek API Key。"
    "请在后端环境变量中设置 DEEPSEEK_API_KEY 以启用真实 AI 对话。）"
)


async def mock_chat(messages: list[dict]) :
    """模拟回复 — API Key 未配置时使用

    从 messages 中取最后一条用户消息构造回复。
    产出 (event, data) 元组序列：一个 token 事件 + done。
    """
    user_msg = messages[-1]["content"] if messages else ""
    reply = MOCK_REPLY_TEMPLATE.format(message=user_msg)
    yield ("token", reply)
    yield ("done", "")


async def deepseek_chat(messages: list[dict]):
    """调用 DeepSeek API（SSE 流式）

    产出 (event, data) 元组序列：
    - ("token", content) — 逐字或逐段文本
    - ("done", "") — 流正常结束
    - ("error", msg) — 发生错误
    """
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.DEEPSEEK_MODEL,
                    "messages": messages,
                    "stream": True,
                },
            ) as response:
                if response.status_code != 200:
                    yield ("error", f"DeepSeek API 返回错误: {response.status_code}")
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield ("token", content)
                    except json.JSONDecodeError:
                        continue

        yield ("done", "")

    except Exception as e:
        yield ("error", f"AI 对话出错: {str(e)}")

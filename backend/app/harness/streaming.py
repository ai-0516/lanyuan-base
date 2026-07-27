"""DeepSeek API 流式客户端 + 模拟回复

职责：
- 模拟回复（无 API Key 时的 fallback）
- DeepSeek API 的 HTTP SSE 请求，支持工具调用
- 逐 token 产出 (event, data) 元组
"""

import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MOCK_REPLY_TEMPLATE = (
    "收到您的消息：「{message}」\n\n"
    "（当前为模拟模式，未配置 DeepSeek API Key。"
    "请在后端环境变量中设置 DEEPSEEK_API_KEY 以启用真实 AI 对话。）"
)


async def mock_chat(messages: list[dict]):
    """模拟回复 — API Key 未配置时使用"""
    user_msg = messages[-1]["content"] if messages else ""
    reply = MOCK_REPLY_TEMPLATE.format(message=user_msg)
    logger.info("LLM request (mock): messages=%d", len(messages))
    yield ("token", reply)
    yield ("done", "")


def _merge_tool_call(
    accumulator: dict[int, dict],
    index: int,
    chunk: dict,
):
    """将流式 chunk 中的 tool_call delta 合并到累加器中"""
    if index not in accumulator:
        accumulator[index] = {
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        }
    tc = chunk.get("tool_calls", [{}])[0]
    if tc.get("id"):
        accumulator[index]["id"] = tc["id"]
    if tc.get("function", {}).get("name"):
        accumulator[index]["function"]["name"] = tc["function"]["name"]
    if tc.get("function", {}).get("arguments"):
        accumulator[index]["function"]["arguments"] += tc["function"]["arguments"]


async def deepseek_chat(messages: list[dict], tools: list[dict] | None = None):
    """调用 DeepSeek API（SSE 流式），支持工具调用

    产出 (event, data) 元组序列：
    - ("token", content) — AI 回复文字
    - ("tool_call", tool_call_dict) — 模型请求调用工具
    - ("done", "") — 流正常结束（无工具调用时）
    - ("error", msg) — 发生错误
    """
    try:
        request_body = {
            "model": settings.DEEPSEEK_MODEL,
            "messages": messages,
            "stream": True,
        }
        if tools:
            request_body["tools"] = tools

        logger.info(
            "LLM request: model=%s messages=%d tools=%s",
            settings.DEEPSEEK_MODEL,
            len(messages),
            "yes" if tools else "no",
        )

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=request_body,
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    body_text = body.decode("utf-8", errors="replace")[:2000]
                    logger.error(
                        "DeepSeek API error: status=%s body=%s",
                        response.status_code, body_text,
                    )
                    yield ("error", f"DeepSeek API 返回错误: {response.status_code}")
                    return

                tool_call_accumulator: dict[int, dict] = {}
                reasoning_content_parts: list[str] = []
                finish_reason: str | None = None
                token_count = 0

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        choice = data.get("choices", [{}])[0]
                        delta = choice.get("delta", {})
                        finish_reason = choice.get("finish_reason")

                        # 思考过程 token（DeepSeek 推理模型）
                        rc = delta.get("reasoning_content", "")
                        if rc:
                            reasoning_content_parts.append(rc)
                            yield ("reasoning_token", rc)

                        # 文本 token
                        content = delta.get("content", "")
                        if content:
                            token_count += 1
                            yield ("token", content)

                        # 工具调用（按 index 合并多 chunk 参数）
                        if delta.get("tool_calls"):
                            for tc_chunk in delta["tool_calls"]:
                                _merge_tool_call(
                                    tool_call_accumulator,
                                    tc_chunk.get("index", 0),
                                    {"tool_calls": [tc_chunk]},
                                )

                    except json.JSONDecodeError:
                        continue

        # 流结束 — 判断是工具调用还是纯文本
        if reasoning_content_parts:
            yield ("reasoning", "".join(reasoning_content_parts))

        if tool_call_accumulator:
            logger.info(
                "LLM response: tokens=%d finish_reason=tool_calls tools=%d",
                token_count,
                len(tool_call_accumulator),
            )
            for tc in tool_call_accumulator.values():
                yield ("tool_call", tc)
        else:
            logger.info(
                "LLM response: tokens=%d finish_reason=stop",
                token_count,
            )
            yield ("done", "")

    except Exception as e:
        logger.error("LLM error: %s", e)
        yield ("error", f"AI 对话出错: {str(e)}")

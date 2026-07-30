"""DeepSeek API 流式客户端 + 模拟回复 + 重试包装

职责：
- 模拟回复（无 API Key 时的 fallback）
- DeepSeek API 的 HTTP SSE 请求，支持工具调用
- 逐 token 产出 (event, data) 元组
- 错误分类为 LLMStatus，可重试的错误自动重试
- 重大错误（SSE 解析失败、断流）记录详细日志到 critical-errors.log
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.harness.errors import LLMStatus, RETRY_CONFIG, HTTP_STATUS_MAP, retry_delay

logger = logging.getLogger(__name__)
# 重大错误专用日志（SSE 解析失败、断流等需要人肉关注的问题）
_critical_logger = logging.getLogger("app.harness.streaming.critical")

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


def _critical_error(code: LLMStatus, details: dict):
    """记录重大错误到 critical-errors.log

    这类错误需要人肉关注排查，记录完整的上下文信息。
    """
    _critical_logger.error(
        "code=%s ts=%s %s",
        code.value,
        datetime.now(timezone.utc).isoformat(),
        json.dumps(details, ensure_ascii=False, default=str),
    )
    logger.error("LLM critical: code=%s details=%s", code.value, details)


async def deepseek_chat(messages: list[dict], tools: list[dict] | None = None):
    """调用 DeepSeek API（SSE 流式），支持工具调用

    产出 (event, data) 元组序列：
    - ("token", content) — AI 回复文字
    - ("tool_call", tool_call_dict) — 模型请求调用工具
    - ("done", "") — 流正常结束（无工具调用时）
    - ("error", dict) — 发生错误，包含 code/message 等结构信息
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
                # ── 非 200 响应 — 分类错误码 ──
                if response.status_code != 200:
                    body = await response.aread()
                    body_text = body.decode("utf-8", errors="replace")[:2000]

                    code = HTTP_STATUS_MAP.get(
                        response.status_code, LLMStatus.UNEXPECTED
                    )
                    if code == LLMStatus.UNEXPECTED:
                        logger.warning(
                            "Unknown HTTP status code=%s mapped to UNEXPECTED",
                            response.status_code,
                        )

                    # 认证失败等关键错误直接记 critical
                    if code in (LLMStatus.AUTH_FAILED, LLMStatus.BAD_REQUEST):
                        _critical_error(code, {
                            "http_status": response.status_code,
                            "body": body_text,
                        })
                    else:
                        logger.error(
                            "DeepSeek API error: status=%s code=%s body=%s",
                            response.status_code, code.value, body_text,
                        )

                    yield ("error", {
                        "code": code,
                        "message": f"DeepSeek API 返回错误 ({code.value})",
                        "http_status": response.status_code,
                    })
                    return

                # ── 200 响应 — 解析 SSE 流 ──
                tool_call_accumulator: dict[int, dict] = {}
                reasoning_content_parts: list[str] = []
                token_count = 0
                usage_data: dict | None = None
                saw_done_signal = False  # 是否收到 [DONE]
                error_chunk_count = 0
                last_error_chunk = ""

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        saw_done_signal = True
                        break
                    try:
                        data = json.loads(data_str)
                        choice = data.get("choices", [{}])[0]
                        delta = choice.get("delta", {})

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
                        error_chunk_count += 1
                        last_error_chunk = data_str[:2000]
                        continue

                    # 捕获 usage（通常在最后一个 chunk 中）
                    if data.get("usage"):
                        usage_data = data["usage"]

        # ── SSE 流结束 — 检查是否异常断流 ──
        if not saw_done_signal and token_count > 0 and not tool_call_accumulator:
            # 收到了 token 但流非正常结束 — 断流
            _critical_error(LLMStatus.SSE_DISCONNECTED, {
                "tokens_before": token_count,
                "has_tool_calls": bool(tool_call_accumulator),
                "tool_call_count": len(tool_call_accumulator),
            })
            yield ("error", {
                "code": LLMStatus.SSE_DISCONNECTED,
                "message": "AI 回复流中断，请重试",
                "tokens_before": token_count,
            })
            return

        if error_chunk_count > 0:
            _critical_error(LLMStatus.SSE_PARSE_ERROR, {
                "error_chunk_count": error_chunk_count,
                "last_chunk": last_error_chunk,
                "tokens_before": token_count,
            })
            # 非 fatal — 继续处理已成功解析的数据

        # ── 流结束 — 判断是工具调用还是纯文本 ──
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
            if usage_data:
                yield ("usage", usage_data)
        else:
            logger.info(
                "LLM response: tokens=%d finish_reason=stop",
                token_count,
            )
            yield ("done", "")
            if usage_data:
                yield ("usage", usage_data)

    except httpx.TimeoutException:
        logger.error("LLM timeout: messages=%d timeout=60s", len(messages))
        yield ("error", {
            "code": LLMStatus.TIMEOUT,
            "message": "AI 请求超时，请重试",
        })

    except httpx.ConnectError as e:
        logger.error("LLM connection error: %s", str(e)[:200])
        yield ("error", {
            "code": LLMStatus.NETWORK_ERROR,
            "message": "AI 服务暂时不可用，请重试",
        })

    except Exception:
        logger.exception("LLM unexpected error")
        yield ("error", {
            "code": LLMStatus.UNEXPECTED,
            "message": "AI 对话出错，请重试",
        })


async def retry_deepseek_chat(messages: list[dict], tools: list[dict] | None = None):
    """带自动重试的 DeepSeek 流式调用

    产出同 deepseek_chat，但：
    - 可重试的错误（429/529/timeout）自动退避重试
    - 重试期间不向前端 yield error 事件
    - 重试耗尽后 yield error 事件
    - 不可重试的错误（401/SSE 断流等）立即 yield error

    **注意**：重试期间会缓存 token，成功后才一次性 yield。
    这意味着重试时前端会短暂无响应，但能保证不输出重复/
    错乱的 token。正常情况（一次成功）不受影响。

    用法与 deepseek_chat 相同，直接替换即可。
    """
    # deepseek_chat 不修改 messages 列表，重试时传入相同的 messages 是安全的
    buffered_events: list[tuple] = []

    # 从 RETRY_CONFIG 取最大重试次数作为 safety limit
    _max_possible = max(
        (cfg["max_retries"] for cfg in RETRY_CONFIG.values() if cfg is not None),
        default=0,
    ) + 1  # +1 为首次尝试

    for attempt in range(_max_possible):
        error_data = None
        buffered_events = []

        async for event, data in deepseek_chat(messages, tools=tools):
            if event == "error":
                error_data = data
                break  # 当前尝试失败，不 yield 给前端
            buffered_events.append((event, data))

        if error_data is None:
            for evt, dat in buffered_events:
                yield (evt, dat)
            return

        err: dict = error_data  # type: ignore[assignment]
        code = err.get("code", LLMStatus.UNEXPECTED)
        config = RETRY_CONFIG.get(code)

        if config is None:
            yield ("error", err)
            return

        max_retries = config.get("max_retries", 3)
        if attempt >= max_retries:
            logger.warning(
                "LLM retry exhausted: code=%s attempts=%d/%d",
                code.value, attempt, max_retries,
            )
            yield ("error", {
                "code": LLMStatus.RETRY_EXHAUSTED,
                "message": f"AI 服务暂时不可用（重试 {attempt}/{max_retries} 次后仍失败），请稍后重试",
                "original_code": code,
            })
            return

        retry_after = err.get("retry_after")
        delay = retry_delay(code, attempt, retry_after=retry_after)
        logger.warning(
            "LLM retry: code=%s attempt=%d/%d delay=%.1fs",
            code.value, attempt + 1, max_retries, delay,
        )
        await asyncio.sleep(delay)

    # safety exit (shouldn't reach here)
    yield ("error", {
        "code": LLMStatus.UNEXPECTED,
        "message": "AI 服务内部错误，请重试",
    })

"""LLM 流式客户端（协议无关编排）+ 模拟回复 + 重试包装

协议差异（URL 后缀 / 认证头 / SSE 行格式 / 消息转换）由 adapters 层封装
（LLMAdapter 子类，TECH_SPEC §5），本模块只做协议无关的编排：
- HTTP 传输（httpx SSE 流）
- 错误分类（LLMStatus / HTTP_STATUS_MAP）
- 重试（RETRY_CONFIG 指数退避 + 413 压缩后重试）
- mock（无 API Key 时的降级路径）

事件契约（与历史 deepseek_chat 一致）：
- ("token", str) / ("reasoning_token", str) / ("reasoning", str)
- ("tool_call", dict) — OpenAI 形状 {id, type, function:{name, arguments}}
- ("done", "") / ("usage", dict) / ("error", dict) / ("fallback", dict)
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.harness import context_compact
from app.harness.adapters import get_adapter, resolve_provider
from app.harness.adapters.messages import Message
from app.harness.errors import HTTP_STATUS_MAP, LLMStatus, RETRY_CONFIG, retry_delay

logger = logging.getLogger(__name__)
# 重大错误专用日志（SSE 解析失败、断流等需要人肉关注的问题）
_critical_logger = logging.getLogger("app.harness.streaming.critical")

MOCK_REPLY_TEMPLATE = (
    "收到您的消息：「{message}」\n\n"
    "（当前为模拟模式，未配置 LLM API Key。"
    "请在后端环境变量中设置 LLM_API_KEY 以启用真实 AI 对话。）"
)


def _last_user_text(messages: list[Message]) -> str:
    """取最后一条 user 消息的文本（canonical content 可能是 str 或 block 列表）"""
    for msg in reversed(messages):
        if msg["role"] == "user":
            content = msg["content"]
            if isinstance(content, str):
                return content
            return "".join(b["text"] for b in content)
    return ""


async def mock_chat(messages: list[Message]):
    """模拟回复 — API Key 未配置时使用"""
    reply = MOCK_REPLY_TEMPLATE.format(message=_last_user_text(messages))
    logger.info("LLM request (mock): messages=%d", len(messages))
    yield ("token", reply)
    yield ("done", "")


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


async def llm_chat(messages: list[Message], tools: list[dict] | None = None):
    """调用 LLM（按 LLM_PROVIDER 选协议），SSE 流式，支持工具调用

    输入 canonical 消息（TECH_SPEC §4），内部经 adapter 转换为协议请求体；
    输出统一事件（协议无关，见模块 docstring）。

    产出 (event, data) 元组序列：
    - ("token", content) — AI 回复文字
    - ("reasoning_token", content) — 思考过程增量
    - ("reasoning", content) — 思考过程合并（流结束）
    - ("tool_call", tool_call_dict) — 模型请求调用工具
    - ("done", "") — 流正常结束（无工具调用时）
    - ("usage", dict) — token 用量
    - ("error", dict) — 发生错误，包含 code/message 等结构信息
    """
    provider = resolve_provider()
    adapter = get_adapter(provider["protocol"])
    base_url = provider["base_url"]
    url = f"{base_url.rstrip('/')}{adapter.endpoint_path}"
    headers = adapter.build_headers()

    request_body: dict = adapter.canonical_to_llm(messages, tools)
    request_body["model"] = provider["model"]
    request_body["stream"] = True
    if getattr(adapter, "DEFAULT_MAX_TOKENS", None):
        request_body[adapter.max_tokens_field] = adapter.DEFAULT_MAX_TOKENS  # Anthropic/Responses 必填

    logger.info(
        "LLM request: provider=%s protocol=%s model=%s messages=%d tools=%s",
        settings.LLM_PROVIDER, adapter.protocol, provider["model"],
        len(messages), "yes" if tools else "no",
    )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                url,
                headers=headers,
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
                            "LLM API error: status=%s code=%s body=%s",
                            response.status_code, code.value, body_text,
                        )

                    yield ("error", {
                        "code": code,
                        "message": f"LLM API 返回错误 ({code.value})",
                        "http_status": response.status_code,
                    })
                    return

                # ── 200 响应 — 解析 SSE 流（协议差异走 adapter）──
                state: dict = {}
                saw_end_signal = False  # [DONE] 或 message_stop
                error_chunk_count = 0
                last_error_chunk = ""

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        data = None  # [DONE] 等非 JSON 结束信号，adapter.is_end 内部处理

                    if adapter.is_end(data_str, data):
                        saw_end_signal = True
                        if data_str == "[DONE]":
                            break
                        if data is not None:
                            # 结束事件可能携带 payload（如 Responses 的 response.completed 带 usage），
                            # 先喂给 adapter 消费再结束（anthropic message_stop 无对应分支，零产出）
                            for event, ev_data in adapter.llm_to_canonical(data, state):
                                yield (event, ev_data)
                        continue

                    if data is None:
                        error_chunk_count += 1
                        last_error_chunk = data_str
                        continue

                    for event, ev_data in adapter.llm_to_canonical(data, state):
                        yield (event, ev_data)

                # ── 流结束 — 收尾事件（reasoning 合并 / tool_call / done / usage）──
                for event, ev_data in adapter.finalize(state):
                    yield (event, ev_data)

                # ── 断流检测 — 收到 token 但流非正常结束 ──
                # 通过 adapter 统一接口查询 state（不直读协议相关 key，review #53）
                has_token = adapter.has_tokens(state)
                has_tool_call = adapter.has_tool_calls(state)
                if not saw_end_signal and has_token and not has_tool_call:
                    _critical_error(LLMStatus.SSE_DISCONNECTED, {
                        "tokens_before": adapter.token_count(state),
                        "has_tool_calls": has_tool_call,
                    })
                    yield ("error", {
                        "code": LLMStatus.SSE_DISCONNECTED,
                        "message": "AI 回复流中断，请重试",
                        "tokens_before": adapter.token_count(state),
                    })
                    return

                if error_chunk_count > 0:
                    _critical_error(LLMStatus.SSE_PARSE_ERROR, {
                        "error_chunk_count": error_chunk_count,
                        "last_chunk": last_error_chunk,
                        "tokens_before": adapter.token_count(state),
                    })
                    # 非 fatal — 继续处理已成功解析的数据

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


async def retry_llm_chat(messages: list[Message], tools: list[dict] | None = None):
    """带自动重试的 LLM 流式调用（协议无关）

    产出同 llm_chat，但：
    - 可重试的错误（429/529/timeout）自动退避重试
    - 不可重试的错误（401/SSE 断流等）立即 yield error
    - 重试耗尽后 yield fallback 事件（降级回复），不再 yield error
    - 重试等待期间 yield token 事件（俏皮文案），作为普通消息展示

    **注意**：正常情况（一次成功）完全是流式的，不缓存。只在重试时（<1%）
    才缓存 token 并一次性 yield，以避免重复输出。

    用法与 llm_chat 相同，直接替换即可。
    """
    # llm_chat 不修改 messages 列表，重试时传入相同的 messages 是安全的

    # safelimit: 最多尝试 max_retries+1 次（attempt 0 为首次，1..max_retries 为重试）
    # max_retries=3 → 共 4 次：attempt 0(首次) → 1(重试1) → 2(重试2) → 3(重试3→耗尽)
    _max_possible = max(
        (cfg["max_retries"] for cfg in RETRY_CONFIG.values() if cfg is not None),
        default=0,
    ) + 1

    for attempt in range(_max_possible):
        error_data = None
        is_retry = attempt > 0
        retry_buf: list[tuple] = []

        async for event, data in llm_chat(messages, tools=tools):
            if event == "error":
                error_data = data
                break
            if is_retry:
                retry_buf.append((event, data))
            else:
                yield (event, data)  # 首次尝试：直接流式

        if error_data is None:
            if is_retry and retry_buf:
                for evt, dat in retry_buf:
                    yield (evt, dat)
            return

        # ── 错误处理 ──
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
            yield ("fallback", {
                "message": "您好，AI 暂时无法回复您的消息，请稍后重试。",
                "original_code": code,
            })
            return

        retry_after = err.get("retry_after")
        delay = retry_delay(code, attempt, retry_after=retry_after)

        # 压缩后重试：413 等场景重试前对 messages 做应急压缩
        # llm_reactive_compact 摘要失败时内部强裁剪兜底，不会抛异常
        if config.get("compress_before_retry"):
            messages[:] = await context_compact.llm_reactive_compact(messages)

        logger.warning(
            "LLM retry: code=%s attempt=%d/%d delay=%.1fs",
            code.value, attempt + 1, max_retries, delay,
        )
        yield ("token", "AI 正在飞速思考中……")
        await asyncio.sleep(delay)

    # safety exit (shouldn't reach here)
    yield ("error", {
        "code": LLMStatus.UNEXPECTED,
        "message": "AI 服务内部错误，请重试",
    })

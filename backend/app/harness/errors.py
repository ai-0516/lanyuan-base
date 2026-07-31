"""LLM 调用错误码与恢复策略

定义 Agent Loop 内所有可能的错误状态码，以及对应的重试/恢复配置。
错误码在 streaming.py 中由 HTTP 状态码或异常类型映射生成，
agent.py 根据恢复策略决定重试或降级。

用法:
    status = LLMStatus.RATE_LIMIT       # 语义明确
    config = RETRY_CONFIG.get(status)   # 恢复策略
"""

import random
from enum import Enum


class LLMStatus(str, Enum):
    """LLM 调用状态/错误码 — 每个错误码都有明确的恢复路径"""

    # ── HTTP 层（来自 API 响应状态码） ──
    RATE_LIMIT = "rate_limit"               # 429 — 限流，可退避重试
    OVERLOADED = "overloaded"               # 529/503 — 过载，可退避重试
    AUTH_FAILED = "auth_failed"             # 401/403 → 直接降级
    PAYLOAD_TOO_LARGE = "payload_too_large" # 413 — 请求体超长，当前直接降级（#8 后支持压缩重试）
    BAD_REQUEST = "bad_request"             # 400 — 请求参数错误，直接降级

    # ── 传输层（来自 httpx 异常或 SSE 解析） ──
    TIMEOUT = "timeout"                     # 连接超时（60s），可重试 1 次
    NETWORK_ERROR = "network_error"         # DNS/拒绝连接，可重试 1 次
    SSE_DISCONNECTED = "sse_disconnected"   # SSE 流中间断开，可重试（retry buffer 已防重复）
    SSE_PARSE_ERROR = "sse_parse_error"     # SSE JSON 解析失败，需重点记录

    # ── 恢复层（重试逻辑内部状态） ──
    RETRY_EXHAUSTED = "retry_exhausted"     # 重试次数耗尽，降级为模拟回复
    UNEXPECTED = "unexpected_error"         # 未分类异常，兜底降级


# ── HTTP 状态码 → LLMStatus 映射（只在 streaming.py 内部使用） ──
HTTP_STATUS_MAP: dict[int, LLMStatus] = {
    429: LLMStatus.RATE_LIMIT,
    529: LLMStatus.OVERLOADED,
    503: LLMStatus.OVERLOADED,
    401: LLMStatus.AUTH_FAILED,
    403: LLMStatus.AUTH_FAILED,
    413: LLMStatus.PAYLOAD_TOO_LARGE,
    400: LLMStatus.BAD_REQUEST,
}


# ── 恢复策略配置 ──
# None = 不可重试
# dict = 重试配置
RETRY_CONFIG: dict[LLMStatus, dict | None] = {
    # ── 可重试 ──
    LLMStatus.RATE_LIMIT: {
        "max_retries": 3,
        "base_delay_ms": 500,
        "jitter": True,
        "description": "指数退避，最多 3 次",
    },
    LLMStatus.OVERLOADED: {
        "max_retries": 3,
        "base_delay_ms": 500,
        "jitter": True,
        "description": "指数退避，最多 3 次",
    },
    LLMStatus.TIMEOUT: {
        "max_retries": 1,
        "base_delay_ms": 1000,
        "jitter": False,
        "description": "固定 1s 等待后重试 1 次",
    },
    LLMStatus.NETWORK_ERROR: {
        "max_retries": 1,
        "base_delay_ms": 1000,
        "jitter": False,
        "description": "固定 1s 等待后重试 1 次",
    },
    LLMStatus.PAYLOAD_TOO_LARGE: {
        "max_retries": 1,
        "base_delay_ms": 0,
        "jitter": False,
        "compress_before_retry": True,
        "description": "413 — 应急压缩上下文后重试 1 次",
    },
    # ── 不可重试（直接降级） ──
    LLMStatus.AUTH_FAILED: None,
    LLMStatus.BAD_REQUEST: None,
    LLMStatus.SSE_DISCONNECTED: {
        "max_retries": 1,
        "base_delay_ms": 0,
        "jitter": False,
        "description": "retry_deepseek_chat 已缓冲，重试不会导致前端重复",
    },
    LLMStatus.SSE_PARSE_ERROR: None,
    LLMStatus.UNEXPECTED: None,
}


def retry_delay(status: LLMStatus, attempt: int, retry_after: int | None = None) -> float:
    """计算重试等待时间（秒）

    参数:
        status: 错误码
        attempt: 当前重试次数（0-based）
        retry_after: 服务器返回的 Retry-After 值（秒），优先使用

    返回:
        等待秒数
    """
    if retry_after is not None and retry_after > 0:
        return float(retry_after)

    config = RETRY_CONFIG.get(status)
    if config is None:
        return 0.0

    base = config.get("base_delay_ms", 500)
    # 指数退避: 500ms → 1000ms → 2000ms → ... 上限 32s
    delay = min(base * (2 ** attempt), 32000) / 1000.0

    if config.get("jitter"):
        delay += random.uniform(0, delay * 0.25)

    return delay

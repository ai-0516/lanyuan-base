"""Provider 映射表（TECH_SPEC §6.2）

声明式 provider 配置：厂商（provider）与协议（protocol）两个正交维度
（review #53 第二轮，snxly 建议）：
- LLM_PROVIDER：厂商维度（deepseek / 未来 qwen/moonshot/zhipu/anthropic）
- LLM_PROTOCOL：协议维度（openai / anthropic，对应 LLMAdapter 子类）

同一厂商可支持多种协议（例：deepseek 同时提供 openai 兼容端点与 anthropic
兼容端点），protocol 决定用哪套协议转换；base_url 默认值按 (provider, protocol)
二元查表，quirk 跟着 provider 走（Hermes ProviderProfile 思想）。
"""

from enum import Enum

from app.config import settings


class Protocol(str, Enum):
    """支持的 LLM 协议（每种对应一个 LLMAdapter 子类）"""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


# provider → 厂商级默认配置（与协议无关的部分）
PROVIDER_DEFAULTS: dict[str, dict] = {
    "deepseek": {
        # default_model: LLM_MODEL 为空时使用
        "default_model": "deepseek-v4-flash",
        # requires_reasoning_echo: DeepSeek V4 强制回传 reasoning_content
        #   （Hermes 调研，2026-08-07）
        "requires_reasoning_echo": True,
    },
    # 未来：qwen/moonshot/zhipu → 各自 default_model/quirk
}

# (provider, protocol) → 该组合的默认 base_url
# LLM_BASE_URL 非空时覆盖以下默认值。
# （DeepSeek 官方文档：openai 模式 base_url 为 https://api.deepseek.com，无 /v1 后缀）
DEFAULT_BASE_URLS: dict[tuple[str, Protocol], str] = {
    ("deepseek", Protocol.OPENAI): "https://api.deepseek.com",
    ("deepseek", Protocol.ANTHROPIC): "https://api.deepseek.com/anthropic",
}


def resolve_provider() -> dict:
    """按 settings.LLM_PROVIDER + settings.LLM_PROTOCOL 解析 provider 完整配置

    LLM_BASE_URL / LLM_MODEL 非空则覆盖默认值（环境变量优先）。

    返回: {provider, protocol, requires_reasoning_echo, base_url, model}
    """
    provider = settings.LLM_PROVIDER
    try:
        protocol = Protocol(settings.LLM_PROTOCOL)
    except ValueError:
        raise ValueError(
            f"未知 LLM_PROTOCOL: {settings.LLM_PROTOCOL}（可选: {', '.join(p.value for p in Protocol)}）"
        ) from None

    defaults = PROVIDER_DEFAULTS.get(provider)
    if defaults is None:
        raise ValueError(
            f"未知 LLM_PROVIDER: {provider}（可选: {', '.join(PROVIDER_DEFAULTS)}）"
        )

    base_url = settings.LLM_BASE_URL or DEFAULT_BASE_URLS.get((provider, protocol), "")
    model = settings.LLM_MODEL or defaults["default_model"]
    return {
        "provider": provider,
        "protocol": protocol,
        "requires_reasoning_echo": defaults["requires_reasoning_echo"],
        "base_url": base_url,
        "model": model,
    }

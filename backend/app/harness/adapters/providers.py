"""Provider 映射表（TECH_SPEC §6.2）

声明式 provider 配置：协议（LLMAdapter 子类）+ 默认 base_url + 协议特有 quirk。
quirk 跟着 provider 走（Hermes ProviderProfile 思想），不散在 adapter 代码里。
"""

from app.config import settings

PROVIDER_PROTOCOLS: dict[str, dict] = {
    # protocol: 协议适配器（LLMAdapter 子类）
    # default_base_url: LLM_BASE_URL 为空时使用
    # requires_reasoning_echo: DeepSeek V4 强制回传 reasoning_content（Hermes 调研，2026-08-07）
    "deepseek": {
        "protocol": "openai",
        "default_base_url": "https://api.deepseek.com/v1",
        "requires_reasoning_echo": True,
    },
    "deepseek-anthropic": {
        "protocol": "anthropic",
        "default_base_url": "https://api.deepseek.com/anthropic",
        "requires_reasoning_echo": True,
    },
    # 未来：qwen/moonshot/zhipu → openai 协议 + 各自 base_url；anthropic → anthropic 协议
}


def resolve_provider() -> dict:
    """按 settings.LLM_PROVIDER 解析协议与 base_url（LLM_BASE_URL 非空则覆盖默认）

    返回: {protocol, default_base_url, requires_reasoning_echo, base_url}
    """
    cfg = PROVIDER_PROTOCOLS.get(settings.LLM_PROVIDER)
    if cfg is None:
        raise ValueError(
            f"未知 LLM_PROVIDER: {settings.LLM_PROVIDER}（可选: {', '.join(PROVIDER_PROTOCOLS)}）"
        )
    base_url = settings.LLM_BASE_URL or cfg["default_base_url"]
    return {**cfg, "base_url": base_url}

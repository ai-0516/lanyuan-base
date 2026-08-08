"""Provider 映射表（TECH_SPEC §6.2）

声明式 provider 配置：协议（LLMAdapter 子类）+ 默认 base_url + 默认 model + 协议特有 quirk。
quirk 跟着 provider 走（Hermes ProviderProfile 思想），不散在 adapter 代码里。

provider 命名规则（review #53）：<厂商>-<协议模式>，如 deepseek-openai / deepseek-anthropic，
与 LLM_PROVIDER 环境变量一一对应，配置来源唯一。
"""

from enum import Enum

from app.config import settings


class Protocol(str, Enum):
    """支持的 LLM 协议（每种对应一个 LLMAdapter 子类）"""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


PROVIDER_PROTOCOLS: dict[str, dict] = {
    # protocol: 协议适配器（LLMAdapter 子类）
    # default_base_url: LLM_BASE_URL 为空时使用
    #   （DeepSeek 官方文档：openai 模式 base_url 为 https://api.deepseek.com，无 /v1 后缀）
    # default_model: LLM_MODEL 为空时使用
    # requires_reasoning_echo: DeepSeek V4 强制回传 reasoning_content（Hermes 调研，2026-08-07）
    "deepseek-openai": {
        "protocol": Protocol.OPENAI,
        "default_base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "requires_reasoning_echo": True,
    },
    "deepseek-anthropic": {
        "protocol": Protocol.ANTHROPIC,
        "default_base_url": "https://api.deepseek.com/anthropic",
        "default_model": "deepseek-v4-flash",
        "requires_reasoning_echo": True,
    },
    # 未来：qwen/moonshot/zhipu → openai 协议 + 各自 base_url；anthropic → anthropic 协议
}


def resolve_provider() -> dict:
    """按 settings.LLM_PROVIDER 解析 provider 完整配置

    LLM_BASE_URL / LLM_MODEL 非空则覆盖 provider 默认值（环境变量优先）。

    返回: {protocol, requires_reasoning_echo, base_url, model}
    """
    cfg = PROVIDER_PROTOCOLS.get(settings.LLM_PROVIDER)
    if cfg is None:
        raise ValueError(
            f"未知 LLM_PROVIDER: {settings.LLM_PROVIDER}（可选: {', '.join(PROVIDER_PROTOCOLS)}）"
        )
    base_url = settings.LLM_BASE_URL or cfg["default_base_url"]
    model = settings.LLM_MODEL or cfg["default_model"]
    return {**cfg, "base_url": base_url, "model": model}

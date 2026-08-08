"""Provider 映射表（TECH_SPEC §6.2）

声明式 provider 配置：厂商（provider）与协议（protocol）两个正交维度
（review #53 第二轮，snxly 建议）：
- LLM_PROVIDER：厂商维度（deepseek / 未来 qwen/moonshot/zhipu/anthropic）
- LLM_PROTOCOL：协议维度（openai / anthropic，对应 LLMAdapter 子类）

配置分层（review #53 第三轮）：
- provider 公共配置（与协议无关）：default_model / quirk（如 requires_reasoning_echo）
- 协议特殊配置（protocols[Protocol]）：每协议一个可扩展 dict，
  目前是 default_base_url，未来可加协议版本、额外 header 等任意字段

resolve_provider() 返回 ProviderConfig（扁平 TypedDict）：公共配置字段 +
协议特殊配置展开合并后的字段。**返回结构对所有 provider/protocol 统一**，
调用方不需要感知「哪层配置来自哪里」——拿到就是一个完整配置对象。
"""

from enum import Enum
from typing import TypedDict

from app.config import settings


class Protocol(str, Enum):
    """支持的 LLM 协议（每种对应一个 LLMAdapter 子类）"""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class ProviderConfig(TypedDict):
    """resolve_provider() 返回类型（扁平结构，调用方契约）

    字段 = provider 公共配置 + 协议特殊配置展开合并。
    新增协议特殊配置项时同步在此声明（协议差异不外溢）。
    """

    provider: str
    protocol: Protocol
    requires_reasoning_echo: bool
    base_url: str
    model: str


# provider → {公共配置, protocols: {Protocol → 协议特殊配置}}
# 公共配置（与协议无关）：default_model / requires_reasoning_echo 等 quirk
# 协议特殊配置：default_base_url（LLM_BASE_URL 非空时覆盖），可扩展其他字段
PROVIDERS: dict[str, dict] = {
    "deepseek": {
        # default_model: LLM_MODEL 为空时使用
        "default_model": "deepseek-v4-flash",
        # requires_reasoning_echo: DeepSeek V4 强制回传 reasoning_content
        #   （Hermes 调研，2026-08-07）
        "requires_reasoning_echo": True,
        "protocols": {
            # DeepSeek 官方文档：openai 模式 base_url 为 https://api.deepseek.com，无 /v1 后缀
            Protocol.OPENAI: {
                "default_base_url": "https://api.deepseek.com",
            },
            Protocol.ANTHROPIC: {
                "default_base_url": "https://api.deepseek.com/anthropic",
            },
        },
    },
    # 未来：qwen/moonshot/zhipu → 各自 default_model/quirk + 协议特殊配置
}


def resolve_provider() -> ProviderConfig:
    """按 settings.LLM_PROVIDER + settings.LLM_PROTOCOL 解析 provider 完整配置

    LLM_BASE_URL / LLM_MODEL 非空则覆盖默认值（环境变量优先）。

    返回: ProviderConfig（扁平结构，协议特殊配置已展开合并）。
    """
    provider = settings.LLM_PROVIDER
    try:
        protocol = Protocol(settings.LLM_PROTOCOL)
    except ValueError:
        raise ValueError(
            f"未知 LLM_PROTOCOL: {settings.LLM_PROTOCOL}（可选: {', '.join(p.value for p in Protocol)}）"
        ) from None

    cfg = PROVIDERS.get(provider)
    if cfg is None:
        raise ValueError(
            f"未知 LLM_PROVIDER: {provider}（可选: {', '.join(PROVIDERS)}）"
        )
    protocol_cfg = cfg.get("protocols", {}).get(protocol, {})

    # default_base_url 已消费进 base_url（LLM_BASE_URL 优先），不重复暴露；
    # 其余协议特殊配置字段展开合并进顶层（未来扩展直接加，同步声明到 ProviderConfig）
    result: ProviderConfig = {
        "provider": provider,
        "protocol": protocol,
        "requires_reasoning_echo": cfg["requires_reasoning_echo"],
        "base_url": settings.LLM_BASE_URL or protocol_cfg.get("default_base_url", ""),
        "model": settings.LLM_MODEL or cfg["default_model"],
    }
    for k, v in protocol_cfg.items():
        if k != "default_base_url":
            result[k] = v  # type: ignore[typeddict-unknown-key]
    return result

"""Adapter 层导出（TECH_SPEC §5/§7）

- messages.py: canonical 消息模型
- providers.py: LLM_PROVIDER 映射表 + resolve_provider
- llm_adapter.py: LLMAdapter 抽象基类
- openai.py / anthropic.py: 协议子类

get_adapter() 按 resolve_provider().protocol 查表，streaming.py 用统一接口。
"""

from app.harness.adapters.anthropic import AnthropicAdapter
from app.harness.adapters.llm_adapter import LLMAdapter
from app.harness.adapters.openai import OpenAIAdapter
from app.harness.adapters.providers import PROVIDER_PROTOCOLS, resolve_provider

__all__ = [
    "LLMAdapter",
    "OpenAIAdapter",
    "AnthropicAdapter",
    "PROVIDER_PROTOCOLS",
    "resolve_provider",
    "get_adapter",
]

_ADAPTERS: dict[str, LLMAdapter] = {
    "openai": OpenAIAdapter(),
    "anthropic": AnthropicAdapter(),
}


def get_adapter(protocol: str) -> LLMAdapter:
    """按协议名拿 adapter 实例（注册表查询，不感知具体格式）"""
    try:
        return _ADAPTERS[protocol]
    except KeyError:
        raise ValueError(f"未知协议: {protocol}（可选: {', '.join(_ADAPTERS)}）") from None

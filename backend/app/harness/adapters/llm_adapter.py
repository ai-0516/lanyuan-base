"""LLMAdapter 抽象基类（TECH_SPEC §5.1）

上层（streaming.py）只关心「canonical 与 LLM 协议互转」，不感知具体格式。
HTTP 传输留在 streaming.py（重试 / mock / 错误分类是协议无关的编排，不进 adapter）。
"""

from abc import ABC, abstractmethod

from app.harness.adapters.messages import Message
from app.harness.adapters.providers import Protocol


class LLMAdapter(ABC):
    """协议适配器：canonical 与 LLM 协议互转，上层不感知具体格式

    HTTP 传输的编排（重试 / mock / 错误分类）留在 streaming.py；
    协议相关的 URL 后缀 / 认证头 / 流结束信号由子类提供。
    """

    protocol: Protocol  # Protocol.OPENAI | Protocol.ANTHROPIC

    # Anthropic API 必填 max_tokens；OpenAI 兼容协议不传（None）
    DEFAULT_MAX_TOKENS: int | None = None

    # ── 协议相关 HTTP 元信息（streaming.py 编排 HTTP 时读取）──

    @property
    def endpoint_path(self) -> str:
        """base_url 后的 API 路径后缀"""
        raise NotImplementedError

    def build_headers(self) -> dict:
        """认证 + 版本头（协议相关）。settings.LLM_API_KEY 由 adapter 自行读取"""
        raise NotImplementedError

    def is_end(self, data_str: str, data: dict | None) -> bool:
        """SSE data 行是否为流结束信号（OpenAI [DONE] / Anthropic message_stop）

        data_str: data 行原文（未解析）；data: 解析后的 JSON（解析失败为 None）。
        协议差异在 adapter 内屏蔽，streaming.py 统一调用。
        """
        raise NotImplementedError

    # ── 转换 ──

    @abstractmethod
    def canonical_to_llm(self, messages: list[Message], tools: list[dict] | None) -> dict:
        """canonical → 该协议请求体的内容部分（messages + 可选 tools）"""

    @abstractmethod
    def llm_to_canonical(self, event: dict, state: dict) -> list[tuple[str, object]]:
        """增量解析：喂一个 SSE 事件 dict → 本次产出的事件列表（可为空）

        跨事件状态（tool_call 累积 / thinking 累积 / usage）由调用方传入的
        ``state`` dict 维护（初始 {}，streaming.py 持有）。流式 token 实时产出。
        """

    @abstractmethod
    def finalize(self, state: dict) -> list[tuple[str, object]]:
        """流结束收尾：reasoning 合并 / tool_call 输出 / done / usage

        统一事件（与 streaming.py 现状一致）：
        ("token", str) / ("reasoning_token", str) / ("reasoning", str) /
        ("tool_call", dict) / ("done", "") / ("usage", dict) / ("error", dict)
        """

"""LLMAdapter 抽象基类（TECH_SPEC §5.1）

上层（streaming.py）只关心「canonical 与 LLM 协议互转」，不感知具体格式。
HTTP 传输留在 streaming.py（重试 / mock / 错误分类是协议无关的编排，不进 adapter）。
"""

from abc import ABC, abstractmethod

from app.harness.adapters.messages import Message


class LLMAdapter(ABC):
    """协议适配器：canonical 与 LLM 协议互转，上层不感知具体格式

    HTTP 传输的编排（重试 / mock / 错误分类）留在 streaming.py；
    协议相关的 URL 后缀 / 认证头 / 流结束信号由子类提供。
    """

    protocol: str  # "openai" | "anthropic"

    # Anthropic API 必填 max_tokens；OpenAI 兼容协议不传（None）
    DEFAULT_MAX_TOKENS: int | None = None

    # ── 协议相关 HTTP 元信息（streaming.py 编排 HTTP 时读取）──

    @property
    def endpoint_path(self) -> str:
        """base_url 后的 API 路径后缀"""
        raise NotImplementedError

    def build_headers(self, api_key: str) -> dict:
        """认证 + 版本头（协议相关）"""
        raise NotImplementedError

    def is_end_signal(self, data_str: str) -> bool:
        """SSE data 行是否为流结束信号（如 OpenAI 的 [DONE]）"""
        raise NotImplementedError

    def is_end_data(self, data: dict) -> bool:
        """SSE data JSON 是否为流结束事件（如 Anthropic 的 message_stop）"""
        raise NotImplementedError

    # ── 转换 ──

    @abstractmethod
    def canonical2llm(self, messages: list[Message], tools: list[dict] | None) -> dict:
        """canonical → 该协议请求体的内容部分（messages + 可选 tools）"""

    @abstractmethod
    def llm2canonical(self, event: dict, state: dict) -> list[tuple[str, object]]:
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

"""Adapter 层单元测试（TECH_SPEC §8.1）

纯函数测试，无 HTTP：
- OpenAIAdapter.canonical_to_llm: canonical → OpenAI 请求体内容
- AnthropicAdapter.canonical_to_llm: canonical → Anthropic 请求体内容（含硬约束）
- llm_to_canonical 增量解析 + finalize: SSE 事件 → 统一事件
"""

from app.harness.adapters.anthropic import AnthropicAdapter
from app.harness.adapters.openai import OpenAIAdapter


def _collect(adapter, events: list[dict]) -> list[tuple]:
    """喂事件序列 + finalize，返回完整统一事件列表（模拟 streaming 循环）"""
    state: dict = {}
    out: list[tuple] = []
    for e in events:
        out.extend(adapter.llm_to_canonical(e, state))
    out.extend(adapter.finalize(state))
    return out


# ═══════════════════════════════════════════════
# OpenAIAdapter.canonical_to_llm
# ═══════════════════════════════════════════════

class TestOpenAICanonical2Llm:

    def test_text_only(self):
        msgs = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ]
        body = OpenAIAdapter().canonical_to_llm(msgs)
        assert body == {
            "messages": [
                {"role": "system", "content": "你是助手"},
                {"role": "user", "content": "你好"},
            ]
        }

    def test_thinking_to_reasoning_content(self):
        """thinking block → 顶层 reasoning_content（DeepSeek V4 回传硬约束）"""
        msgs = [{
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "思考中"},
                {"type": "text", "text": "回答"},
            ],
        }]
        entry = OpenAIAdapter().canonical_to_llm(msgs)["messages"][0]
        assert entry["reasoning_content"] == "思考中"
        assert entry["content"] == "回答"  # 字符串化，非 block 数组

    def test_tool_call_arguments_json_string(self):
        """toolCall arguments 对象 → JSON 字符串（OpenAI 格式）"""
        msgs = [{
            "role": "assistant",
            "content": [{
                "type": "toolCall", "id": "call_1", "name": "get_weather",
                "arguments": {"city": "北京"},
            }],
        }]
        entry = OpenAIAdapter().canonical_to_llm(msgs)["messages"][0]
        assert entry["content"] is None
        assert entry["tool_calls"] == [{
            "id": "call_1", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "北京"}'},
        }]

    def test_text_and_tool_call_coexist(self):
        """text + toolCall 共存：content 字符串 + tool_calls 数组"""
        msgs = [{
            "role": "assistant",
            "content": [
                {"type": "text", "text": "我来查一下"},
                {"type": "toolCall", "id": "c1", "name": "t1", "arguments": {}},
            ],
        }]
        entry = OpenAIAdapter().canonical_to_llm(msgs)["messages"][0]
        assert entry["content"] == "我来查一下"
        assert len(entry["tool_calls"]) == 1

    def test_tool_result_role_tool(self):
        msgs = [{
            "role": "toolResult", "tool_call_id": "call_1", "content": "晴，25°C",
        }]
        entry = OpenAIAdapter().canonical_to_llm(msgs)["messages"][0]
        assert entry == {"role": "tool", "tool_call_id": "call_1", "content": "晴，25°C"}


# ═══════════════════════════════════════════════
# AnthropicAdapter.canonical_to_llm
# ═══════════════════════════════════════════════

class TestAnthropicCanonical2Llm:

    def test_system_extracted_to_top(self):
        """system 提取到顶层，不进 messages"""
        msgs = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ]
        body = AnthropicAdapter().canonical_to_llm(msgs)
        assert body["system"] == "你是助手"
        assert body["messages"] == [
            {"role": "user", "content": [{"type": "text", "text": "你好"}]}
        ]

    def test_tool_call_to_tool_use(self):
        msgs = [{
            "role": "assistant",
            "content": [{
                "type": "toolCall", "id": "call_1", "name": "get_weather",
                "arguments": {"city": "北京"},
            }],
        }]
        entry = AnthropicAdapter().canonical_to_llm(msgs)["messages"][0]
        assert entry == {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call_1", "name": "get_weather",
                         "input": {"city": "北京"}}],
        }

    def test_tool_result_merged_into_one_user_message(self):
        """一次调多个工具 → 连续 toolResult 合并到同一条 user 消息（Anthropic 合法）"""
        msgs = [
            {"role": "assistant", "content": [
                {"type": "toolCall", "id": "c1", "name": "t1", "arguments": {}},
                {"type": "toolCall", "id": "c2", "name": "t2", "arguments": {}},
            ]},
            {"role": "toolResult", "tool_call_id": "c1", "content": "结果1"},
            {"role": "toolResult", "tool_call_id": "c2", "content": "结果2"},
        ]
        messages = AnthropicAdapter().canonical_to_llm(msgs)["messages"]
        # 两条连续 toolResult 合并成一条 user 消息（含两个 tool_result block）
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) == 1
        assert [b["type"] for b in user_msgs[0]["content"]] == ["tool_result", "tool_result"]
        assert [b["tool_use_id"] for b in user_msgs[0]["content"]] == ["c1", "c2"]

    def test_tool_result_not_merged_with_text_user(self):
        """tool_result user 消息与纯文本 user 互不合并（硬约束 1）"""
        msgs = [
            {"role": "assistant", "content": [{"type": "toolCall", "id": "c1", "name": "t1", "arguments": {}}]},
            {"role": "toolResult", "tool_call_id": "c1", "content": "结果"},
            {"role": "user", "content": "追问"},
        ]
        messages = AnthropicAdapter().canonical_to_llm(msgs)["messages"]
        assert messages[-1] == {"role": "user", "content": [{"type": "text", "text": "追问"}]}

    def test_empty_content_placeholder(self):
        """空 content → "(no output)" 占位（Anthropic 拒绝空 content）"""
        msgs = [{"role": "toolResult", "tool_call_id": "c1", "content": ""}]
        body = AnthropicAdapter().canonical_to_llm(msgs)
        tr = body["messages"][0]["content"][0]
        assert tr["content"] == "(no output)"

    def test_is_error_passed(self):
        """is_error=True → tool_result 携带 is_error（Anthropic 原生字段）"""
        msgs = [{"role": "toolResult", "tool_call_id": "c1", "content": "失败", "is_error": True}]
        tr = AnthropicAdapter().canonical_to_llm(msgs)["messages"][0]["content"][0]
        assert tr["is_error"] is True

    def test_tools_conversion_and_dedup(self):
        """OpenAI 形状 tools → Anthropic input_schema；重复名去重"""
        tools = [
            {"type": "function", "function": {"name": "a", "description": "A", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "a", "description": "A dup", "parameters": {}}},
        ]
        converted = AnthropicAdapter()._convert_tools(tools)
        assert converted == [
            {"name": "a", "description": "A", "input_schema": {"type": "object"}},
        ]

    def test_blank_text_block_skipped(self):
        """空 text block 跳过（Anthropic 拒绝空块）"""
        msgs = [{"role": "assistant", "content": [
            {"type": "text", "text": "   "},
            {"type": "text", "text": "有效"},
        ]}]
        blocks = AnthropicAdapter().canonical_to_llm(msgs)["messages"][0]["content"]
        assert blocks == [{"type": "text", "text": "有效"}]


# ═══════════════════════════════════════════════
# OpenAIAdapter.llm_to_canonical（增量 + finalize）
# ═══════════════════════════════════════════════

class TestOpenAILlm2Canonical:

    def test_text_stream(self):
        events = [
            {"choices": [{"delta": {"content": "你"}}]},
            {"choices": [{"delta": {"content": "好"}}]},
        ]
        out = _collect(OpenAIAdapter(), events)
        assert out == [("token", "你"), ("token", "好"), ("done", "")]

    def test_reasoning_and_done(self):
        events = [
            {"choices": [{"delta": {"reasoning_content": "思考"}}]},
            {"choices": [{"delta": {"content": "回答"}}]},
        ]
        out = _collect(OpenAIAdapter(), events)
        assert ("reasoning_token", "思考") in out
        assert out[-1] == ("done", "")
        # reasoning 合并事件在 finalize 产出
        assert ("reasoning", "思考") in out

    def test_tool_calls_multi_chunk_merge(self):
        """tool_calls 跨 chunk 按 index 合并 arguments"""
        events = [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "get_weather", "arguments": '{"city":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '"北京"}'}}]}}]},
        ]
        out = _collect(OpenAIAdapter(), events)
        tool_calls = [d for e, d in out if e == "tool_call"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["function"]["arguments"] == '{"city":"北京"}'
        assert not any(e == "done" for e, _ in out)  # 有 tool_call 不产 done

    def test_usage_captured(self):
        events = [
            {"choices": [{"delta": {"content": "好"}}], "usage": {"total_tokens": 10}},
        ]
        out = _collect(OpenAIAdapter(), events)
        assert ("usage", {"total_tokens": 10}) in out

    def test_empty_stream_no_done(self):
        """无任何事件 → 不产 done（空流由 streaming 层断流检测兜底）"""
        out = _collect(OpenAIAdapter(), [])
        assert out == []


# ═══════════════════════════════════════════════
# AnthropicAdapter.llm_to_canonical（增量 + finalize）
# ═══════════════════════════════════════════════

class TestAnthropicLlm2Canonical:

    def _tool_stream(self) -> list[dict]:
        """一次完整的 tool_use 流（input_json_delta 跨 chunk 拼装）"""
        return [
            {"type": "message_start", "message": {"usage": {"input_tokens": 10, "output_tokens": 0}}},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "get_weather"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"city":'},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '"北京"}'},
            },
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 8}},
            {"type": "message_stop"},
        ]

    def test_text_stream(self):
        events = [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "你"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "好"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_stop"},
        ]
        out = _collect(AnthropicAdapter(), events)
        assert out == [("token", "你"), ("token", "好"), ("done", "")]

    def test_thinking_delta(self):
        events = [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "思考中"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "回答"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "message_stop"},
        ]
        out = _collect(AnthropicAdapter(), events)
        assert ("reasoning_token", "思考中") in out
        assert ("reasoning", "思考中") in out
        assert ("token", "回答") in out

    def test_tool_use_partial_json_assembly(self):
        """input_json_delta 跨 chunk 拼装 → tool_call（OpenAI 形状事件）"""
        out = _collect(AnthropicAdapter(), self._tool_stream())
        tool_calls = [d for e, d in out if e == "tool_call"]
        assert len(tool_calls) == 1
        assert tool_calls[0] == {
            "id": "toolu_1", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "北京"}'},
        }
        assert not any(e == "done" for e, _ in out)
        # usage 汇总（input + output）
        usage = [d for e, d in out if e == "usage"]
        assert usage and usage[0]["input_tokens"] == 10 and usage[0]["output_tokens"] == 8

    def test_inline_error_event(self):
        events = [{"type": "error", "error": {"type": "overloaded_error", "message": "过载"}}]
        out = _collect(AnthropicAdapter(), events)
        assert out == [("error", {"code": "overloaded_error", "message": "过载"})]

    def test_tool_result_is_error_roundtrip(self):
        """is_error → to_anthropic 携带 →（对称性验证）"""
        msgs = [{"role": "toolResult", "tool_call_id": "c1", "content": "失败", "is_error": True}]
        body = AnthropicAdapter().canonical_to_llm(msgs)
        tr = body["messages"][0]["content"][0]
        assert tr["type"] == "tool_result" and tr["is_error"] is True


# ═══════════════════════════════════════════
# is_end 统一接口（review #53：屏蔽 openai/anthropic 结束信号差异）
# ═══════════════════════════════════════════

class TestIsEnd:
    def test_openai_done_signal(self):
        """OpenAI：[DONE] 原始行 → 结束（data 为 None，因为 [DONE] 不是 JSON）"""
        assert OpenAIAdapter().is_end("[DONE]", None) is True

    def test_openai_regular_data_not_end(self):
        assert OpenAIAdapter().is_end("{" + '"choices":[{"delta":{"content":"hi"}}]}' + "}", {"choices": []}) is False

    def test_anthropic_message_stop(self):
        """Anthropic：message_stop 事件 dict → 结束"""
        assert AnthropicAdapter().is_end("", {"type": "message_stop"}) is True

    def test_anthropic_other_event_not_end(self):
        assert AnthropicAdapter().is_end("", {"type": "content_block_delta"}) is False
        assert AnthropicAdapter().is_end("", None) is False


# ═══════════════════════════════════════════
# Protocol enum（review #53：类型化协议枚举 + get_adapter 注册表）
# ═══════════════════════════════════════════

class TestProtocol:
    def test_enum_values(self):
        from app.harness.adapters.providers import Protocol

        assert Protocol.OPENAI.value == "openai"
        assert Protocol.ANTHROPIC.value == "anthropic"

    def test_get_adapter_by_protocol(self):
        from app.harness.adapters import get_adapter
        from app.harness.adapters.providers import Protocol

        assert get_adapter(Protocol.OPENAI).protocol is Protocol.OPENAI
        assert get_adapter(Protocol.ANTHROPIC).protocol is Protocol.ANTHROPIC

    def test_resolve_provider_returns_model_and_base_url(self):
        from app.harness.adapters.providers import PROVIDERS, Protocol, resolve_provider

        cfg = resolve_provider()
        # 默认 provider/protocol（deepseek + openai）→ model 与 base_url 都有值
        assert cfg["provider"] in PROVIDERS
        assert isinstance(cfg["protocol"], Protocol)
        assert cfg["model"]
        assert cfg["base_url"]
        # (provider, protocol) 组合必须能查到协议特殊配置（含 default_base_url）
        assert cfg["protocol"] in PROVIDERS[cfg["provider"]]["protocols"]
        assert cfg["protocol_config"].get("default_base_url")

    def test_provider_protocol_config_extensible(self):
        """协议特殊配置是可扩展 dict（不只有 url，review #53 第二轮）"""
        from app.harness.adapters.providers import PROVIDERS, Protocol

        proto_cfg = PROVIDERS["deepseek"]["protocols"][Protocol.OPENAI]
        assert isinstance(proto_cfg, dict)
        assert "default_base_url" in proto_cfg  # 目前字段
        # 未来扩展：直接往该 dict 加字段即可，resolve_provider 原样透传 protocol_config

    def test_resolve_provider_unknown_protocol(self):
        """未知 protocol → ValueError（review #53 第二轮：协议独立校验）"""

        from app.config import settings

        from app.harness.adapters.providers import resolve_provider

        old = settings.LLM_PROTOCOL
        try:
            settings.LLM_PROTOCOL = "not-a-protocol"
            try:
                resolve_provider()
                raise AssertionError("应抛出 ValueError")
            except ValueError as e:
                assert "LLM_PROTOCOL" in str(e)
        finally:
            settings.LLM_PROTOCOL = old

    def test_build_headers_no_api_key_param(self):
        """build_headers() 无参（adapter 内部读 settings，review #53）"""
        oa, aa = OpenAIAdapter(), AnthropicAdapter()
        assert "Authorization" in oa.build_headers()
        assert "x-api-key" in aa.build_headers()
        assert "anthropic-version" in aa.build_headers()

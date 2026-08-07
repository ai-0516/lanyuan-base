"""上下文压缩管线单元测试

测试目标：
- snip_message_compact: 消息数超限裁剪中间、配对不拆散、system 保留
- tool_result_compact: 旧 tool 结果占位、最近 N 个保留
- llm_compact: LLM 摘要、摘要失败跳过
- llm_reactive_compact: 保留尾部 + 摘要、摘要失败强裁剪兜底
- estimate_tokens: 字符近似估算
"""

import json

import pytest

from app.harness import context_compact
from app.harness.context_compact import (
    LLMSummaryError,
    KEEP_HEAD,
    KEEP_TAIL,
    TOOL_RESULT_PLACEHOLDER,
    TOOL_RESULT_SNIP_LENGTH,
)


# ── 消息构造 helper（canonical 格式，TECH_SPEC §4） ──

def _system(text: str) -> dict:
    return {"role": "system", "content": text}


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _tool_call_msg(call_id: str, name: str = "test_tool") -> dict:
    return {
        "role": "assistant",
        "content": [{"type": "toolCall", "id": call_id, "name": name, "arguments": {}}],
    }


def _tool_result(call_id: str, content: str) -> dict:
    return {"role": "toolResult", "tool_call_id": call_id, "content": content}


def _long_tool_result(call_id: str) -> dict:
    """超过占位长度阈值的大 tool 结果"""
    return _tool_result(call_id, "x" * (TOOL_RESULT_SNIP_LENGTH + 50))


# ═══════════════════════════════════════════════
# snip_message_compact — L1
# ═══════════════════════════════════════════════

class TestSnipCompact:

    def test_under_threshold_unchanged(self):
        """消息数 ≤ 阈值不裁剪"""
        messages = [_system("sys")] + [_user(f"u{i}") for i in range(20)]
        assert context_compact.snip_message_compact(messages) == messages

    def test_trims_middle_keeps_head_tail(self):
        """超阈值 → 裁剪中间，保留头部+尾部，插入占位符"""
        messages = [_system("sys")] + [_user(f"u{i}") for i in range(60)]
        result = context_compact.snip_message_compact(messages)

        assert result[0] == messages[0], "system 必须保留在第一条"
        assert result[1] == messages[1], "头部消息保留"
        assert result[-1] == messages[-1], "尾部最后一条保留"
        assert result[-2] == messages[-2], "尾部倒数第二条保留"

        placeholders = [
            m for m in result
            if isinstance(m.get("content"), str) and m["content"].startswith("[snipped")
        ]
        assert len(placeholders) == 1, "应有 1 条裁剪占位符"
        assert "snipped" in placeholders[0]["content"]

    def test_head_boundary_keeps_tool_pair(self):
        """头部边界：最后一条头部消息是 tool_call → 吞并其 tool 结果，不拆散"""
        rest = [_user(f"u{i}") for i in range(55)]
        # 让 rest[2] (head_end-1) 是 tool_call，rest[3] 是其 tool 结果
        rest[2] = _tool_call_msg("call_head")
        rest[3] = _tool_result("call_head", "head result")
        messages = [_system("sys")] + rest

        result = context_compact.snip_message_compact(messages)

        # rest[2]/rest[3] 配对必须都在结果里
        contents = [json.dumps(m, ensure_ascii=False) for m in result]
        assert any(json.dumps(_tool_call_msg("call_head"), ensure_ascii=False) == c for c in contents)
        assert any("head result" in c for c in contents)

    def test_tail_boundary_keeps_tool_pair(self):
        """尾部边界：tail 第一条是 tool 结果且前一条是 tool_call → 从 tool_call 开始保留"""
        rest = [_user(f"u{i}") for i in range(57)]
        # tail_start = 57 - 47 = 10 → rest[10] 是 tool 结果、rest[9] 是 tool_call
        rest[9] = _tool_call_msg("call_tail")
        rest[10] = _tool_result("call_tail", "tail result")
        messages = [_system("sys")] + rest

        result = context_compact.snip_message_compact(messages)

        contents = [json.dumps(m, ensure_ascii=False) for m in result]
        assert any("tail result" in c for c in contents)
        assert any("call_tail" in c for c in contents), "tool_call 前驱必须一并保留"

    def test_empty_and_system_only(self):
        """空数组 / 只有 system 不崩溃"""
        assert context_compact.snip_message_compact([]) == []
        only_system = [_system("sys")]
        assert context_compact.snip_message_compact(only_system) == only_system


# ═══════════════════════════════════════════════
# tool_result_compact — L2
# ═══════════════════════════════════════════════

class TestMicroCompact:

    def test_under_threshold_unchanged(self):
        """tool 结果 ≤ 3 个不占位"""
        messages = [
            _tool_call_msg("c0"), _long_tool_result("c0"),
            _tool_call_msg("c1"), _long_tool_result("c1"),
            _tool_call_msg("c2"), _long_tool_result("c2"),
        ]
        assert context_compact.tool_result_compact(messages) == messages

    def test_old_results_placeholder_recent_kept(self):
        """超过 3 个 → 更早的大结果占位，最近 3 个保留"""
        messages = []
        for i in range(5):
            messages.append(_tool_call_msg(f"c{i}"))
            messages.append(_long_tool_result(f"c{i}"))

        result = context_compact.tool_result_compact(messages)

        # 前 2 组占位（索引 1、3），后 3 组保留（索引 5、7、9）
        assert result[1]["content"] == TOOL_RESULT_PLACEHOLDER
        assert result[3]["content"] == TOOL_RESULT_PLACEHOLDER
        assert result[5]["content"].startswith("x")
        assert result[7]["content"].startswith("x")
        assert result[9]["content"].startswith("x")
        # 消息结构未被破坏（tool_call 前驱仍在原位）
        assert result[0]["role"] == "assistant" and result[0]["content"]
        # 不原地修改传入列表（review 问题 2 回归：接口风格与其他函数一致）
        assert messages[1]["content"].startswith("x"), "原列表不应被修改"
        assert messages[3]["content"].startswith("x")

    def test_short_content_kept(self):
        """旧结果但内容短（≤120）不占位"""
        messages = []
        for i in range(4):
            messages.append(_tool_call_msg(f"c{i}"))
            messages.append(_tool_result(f"c{i}", "short"))

        result = context_compact.tool_result_compact(messages)
        assert result[1]["content"] == "short"
        assert result[3]["content"] == "short"


# ═══════════════════════════════════════════════
# llm_compact — L4
# ═══════════════════════════════════════════════

class TestCompactHistory:

    @pytest.mark.asyncio
    async def test_summary_keeps_recent_tail(self, monkeypatch):
        """摘要成功 → system + [Compacted] 摘要 + 尾部最近消息（最新 user 消息保留）"""
        async def _fake_summarize(messages):
            return "用户想要发布一篇帖子，已确认标题内容"

        monkeypatch.setattr(context_compact, "_summarize", _fake_summarize)
        messages = [_system("sys")] + [_user(f"u{i}") for i in range(10)]

        result = await context_compact.llm_compact(messages)

        assert len(result) == 1 + 1 + KEEP_TAIL
        assert result[0] == messages[0], "system 保留"
        assert result[1]["role"] == "user"
        assert result[1]["content"].startswith("[Compacted]")
        assert "用户想要发布一篇帖子" in result[1]["content"]
        # 最新 user 消息必须保留（review 问题 1 回归：L4 不能丢最新意图）
        assert result[-1] == messages[-1]
        assert result[-2] == messages[-2]

    @pytest.mark.asyncio
    async def test_summary_input_excludes_tail(self, monkeypatch):
        """摘要 LLM 只收到更早的历史，尾部（含最新 user 消息）不参与摘要"""
        captured: dict = {}

        async def _fake_summarize(messages):
            captured["msgs"] = messages
            return "摘要"

        monkeypatch.setattr(context_compact, "_summarize", _fake_summarize)
        messages = [_system("sys")] + [_user(f"u{i}") for i in range(10)]

        await context_compact.llm_compact(messages)

        assert len(captured["msgs"]) == len(messages) - 1 - KEEP_TAIL
        assert captured["msgs"][-1]["content"] == "u4", "摘要输入截止到 head"
        assert "u9" not in [m["content"] for m in captured["msgs"]], "最新消息不进摘要"

    @pytest.mark.asyncio
    async def test_summary_failure_skips(self, monkeypatch):
        """摘要失败 → 返回原 messages（跳过压缩，由 reactive 兜底）"""
        async def _fail(messages):
            raise LLMSummaryError("mock summary failure")

        monkeypatch.setattr(context_compact, "_summarize", _fail)
        messages = [_system("sys")] + [_user(f"u{i}") for i in range(10)]

        result = await context_compact.llm_compact(messages)
        assert result == messages

    @pytest.mark.asyncio
    async def test_no_system_messages(self, monkeypatch):
        """无 system 消息且消息数 ≤ 尾部阈值 → 无可压缩历史，原样返回"""
        async def _fake_summarize(messages):
            return "摘要"

        monkeypatch.setattr(context_compact, "_summarize", _fake_summarize)
        messages = [_user(f"u{i}") for i in range(5)]

        result = await context_compact.llm_compact(messages)
        assert result == messages, "head 为空时应原样返回"


# ═══════════════════════════════════════════════
# llm_reactive_compact — 413 应急
# ═══════════════════════════════════════════════

class TestReactiveCompact:

    @pytest.mark.asyncio
    async def test_summary_plus_tail(self, monkeypatch):
        """摘要成功 → system + [Reactive compact] 摘要 + 尾部 5 条"""
        async def _fake_summarize(messages):
            return "前文摘要"

        monkeypatch.setattr(context_compact, "_summarize", _fake_summarize)
        messages = [_system("sys")] + [_user(f"u{i}") for i in range(20)]

        result = await context_compact.llm_reactive_compact(messages)

        assert len(result) == 1 + 1 + KEEP_TAIL
        assert result[0]["role"] == "system"
        assert result[1]["content"].startswith("[Reactive compact]")
        assert "前文摘要" in result[1]["content"]
        assert result[-1] == messages[-1], "尾部最近消息保留"

    @pytest.mark.asyncio
    async def test_tail_boundary_keeps_tool_pair(self, monkeypatch):
        """尾部边界：tool 结果与前驱 tool_call 一起保留"""
        async def _fake_summarize(messages):
            return "摘要"

        monkeypatch.setattr(context_compact, "_summarize", _fake_summarize)
        messages = [_system("sys")] + [_user(f"u{i}") for i in range(20)]
        # rest 索引 14=tool_call、15=tool_result（messages 索引 15、16）
        # tail_start = 20 - 5 = 15 → 恰好指向 tool_result，配对保护应回退到 14
        messages[16] = _tool_result("call_t", "tail result")
        messages[15] = _tool_call_msg("call_t")

        result = await context_compact.llm_reactive_compact(messages)

        contents = [json.dumps(m, ensure_ascii=False) for m in result]
        assert any("tail result" in c for c in contents)
        assert any("call_t" in c for c in contents), "tool_call 前驱必须一并保留"

    @pytest.mark.asyncio
    async def test_summary_failure_force_trim(self, monkeypatch):
        """摘要失败 → 强裁剪兜底：system + 前 3 条 + 尾部 5 条"""
        async def _fail(messages):
            raise LLMSummaryError("mock failure")

        monkeypatch.setattr(context_compact, "_summarize", _fail)
        messages = [_system("sys")] + [_user(f"u{i}") for i in range(20)]

        result = await context_compact.llm_reactive_compact(messages)

        assert len(result) == 1 + KEEP_HEAD + KEEP_TAIL
        assert result[0]["role"] == "system"
        assert result[1] == messages[1], "兜底保留头部前 3 条"
        assert result[-1] == messages[-1]


# ═══════════════════════════════════════════════
# estimate_tokens
# ═══════════════════════════════════════════════

class TestEstimateTokens:

    def test_character_count(self):
        """估算 = json 序列化长度"""
        messages = [{"role": "user", "content": "你好"}]
        assert context_compact.estimate_tokens(messages) == len(
            json.dumps(messages, ensure_ascii=False)
        )

    def test_empty(self):
        assert context_compact.estimate_tokens([]) == 2  # json "[]"

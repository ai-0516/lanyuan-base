"""
AIAgent 多轮调用事件流测试

核心场景（issue #22）：多轮调用（agent loop 多轮）时，每条 LLM 回复之间
必须有 message:start 边界事件，前端才能把多条回复显示为独立气泡，
而不是拼成一条。

协议约定：
- turn=0 的首条回复不发 message:start（前端 onSend 已预创建气泡）
- turn>0 的轮次，在首个 token 前发 message:start
"""

import pytest

from app.harness.agent import AIAgent


class TestMultiTurnEventStream:
    """多轮调用事件流"""

    async def _run(self, fake_source, tool_executor=None):
        """注入 fake LLM source，跑一轮 agent，返回 (event, data) 列表"""
        import app.harness.streaming as S

        async def _ok_executor(*a, **k):
            return "ok"

        original = S.mock_chat
        S.mock_chat = fake_source
        try:
            agent = AIAgent(
                tools=[{"type": "function", "function": {"name": "dummy"}}],
                tool_executor=tool_executor or _ok_executor,
            )
            events = []
            async for event, data in agent.run(
                [{"role": "user", "content": "你好"}]
            ):
                events.append((event, data))
            return events
        finally:
            S.mock_chat = original

    @pytest.mark.asyncio
    async def test_multi_turn_emits_message_start_between_replies(self):
        """两轮回复：message:start 恰好一次，位于第二轮首个 token 之前"""
        async def multi_turn(messages, **kw):
            # messages 已回填 tool 结果（含 role=tool）→ 第二轮
            if any(m.get("role") == "tool" for m in messages):
                yield ("token", "第二轮：查到了")
                yield ("done", "")
            else:
                yield ("token", "第一轮：我来查一下")
                yield ("tool_call", {"id": "call_1", "function": {"name": "dummy", "arguments": "{}"}})

        events = await self._run(multi_turn)

        tokens = [d for e, d in events if e == "token"]
        start_idx = [i for i, (e, _) in enumerate(events) if e == "message:start"]

        # 两轮文字都完整产出
        assert tokens == ["第一轮：我来查一下", "第二轮：查到了"]
        # 恰好一个 message:start
        assert len(start_idx) == 1
        # message:start 位于第二个 token 之前
        second_token_idx = next(
            i for i, (e, _) in enumerate(events) if e == "token" and i > 0
        )
        assert start_idx[0] < second_token_idx

    @pytest.mark.asyncio
    async def test_no_message_start_when_first_turn_has_no_token(self):
        """首轮纯 tool_call（无文字）：message:start 在第二轮首个 token 前"""
        async def tool_only_first(messages, **kw):
            # messages 已回填 tool 结果（含 role=tool）→ 第二轮
            if any(m.get("role") == "tool" for m in messages):
                yield ("token", "第二轮：查到了")
                yield ("done", "")
            else:
                yield ("tool_call", {"id": "call_1", "function": {"name": "dummy", "arguments": "{}"}})

        events = await self._run(tool_only_first)

        tokens = [d for e, d in events if e == "token"]
        start_idx = [i for i, (e, _) in enumerate(events) if e == "message:start"]

        assert tokens == ["第二轮：查到了"]
        assert len(start_idx) == 1
        assert start_idx[0] < next(i for i, (e, _) in enumerate(events) if e == "token")

    @pytest.mark.asyncio
    async def test_single_turn_emits_no_message_start(self):
        """单轮回复（无工具调用）：不产生 message:start（回归保护）"""
        async def single_turn(messages, **kw):
            yield ("token", "你好")
            yield ("done", "")

        events = await self._run(single_turn)

        assert [e for e, _ in events if e == "message:start"] == []
        assert ("token", "你好") in events
        assert ("done", "") in events

    @pytest.mark.asyncio
    async def test_fallback_on_second_turn_emits_message_start(self):
        """第二轮 fallback 降级：message:start 在降级文案前（#22 fallback 路径）"""
        fallback_msg = "您好，AI 暂时无法回复您的消息，请稍后重试。"

        async def fallback_second_turn(messages, **kw):
            # 第二轮 LLM 调用失败 → fallback 降级回复
            if any(m.get("role") == "tool" for m in messages):
                yield ("fallback", {"message": fallback_msg})
            else:
                yield ("token", "第一轮：我来查一下")
                yield ("tool_call", {"id": "call_1", "function": {"name": "dummy", "arguments": "{}"}})

        events = await self._run(fallback_second_turn)

        tokens = [d for e, d in events if e == "token"]
        start_idx = [i for i, (e, _) in enumerate(events) if e == "message:start"]

        # 两轮文字都完整产出（fallback 文案是独立 message）
        assert tokens == ["第一轮：我来查一下", fallback_msg]
        # 恰好一个 message:start，位于降级文案 token 之前
        assert len(start_idx) == 1
        assert start_idx[0] < next(
            i for i, (e, data) in enumerate(events)
            if e == "token" and data == fallback_msg
        )

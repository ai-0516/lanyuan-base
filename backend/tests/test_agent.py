"""
AIAgent 多轮调用事件流测试

核心场景（issue #22）：每条 AI 回复以 message:start 为边界事件，
前端据此创建气泡，多轮回复不会拼成一条。

协议约定：
- 每轮 LLM 回复（有 token 的轮次）在首个 token 前发 message:start，含 turn=0
- 纯 tool_call 轮次无 token → 不发（前端不建气泡，无文字可显示）
- fallback 降级回复同样发 message:start（它也是一条独立 message）
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
    async def test_multi_turn_emits_message_start_per_reply(self):
        """两轮回复：每轮首个 token 前各发一次 message:start"""
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
        starts = [i for i, (e, _) in enumerate(events) if e == "message:start"]

        # 两轮文字都完整产出
        assert tokens == ["第一轮：我来查一下", "第二轮：查到了"]
        # 两条回复 → 两个 message:start，各位于对应轮次的首个 token 之前
        assert len(starts) == 2
        assert starts[0] < next(i for i, (e, _) in enumerate(events) if e == "token")
        assert starts[1] < next(
            i for i, (e, _) in enumerate(events) if e == "token" and i > starts[1]
        )

    @pytest.mark.asyncio
    async def test_no_message_start_when_first_turn_has_no_token(self):
        """首轮纯 tool_call（无 token）：不发 message:start，第二轮 token 前发"""
        async def tool_only_first(messages, **kw):
            # messages 已回填 tool 结果（含 role=tool）→ 第二轮
            if any(m.get("role") == "tool" for m in messages):
                yield ("token", "第二轮：查到了")
                yield ("done", "")
            else:
                yield ("tool_call", {"id": "call_1", "function": {"name": "dummy", "arguments": "{}"}})

        events = await self._run(tool_only_first)

        tokens = [d for e, d in events if e == "token"]
        starts = [i for i, (e, _) in enumerate(events) if e == "message:start"]

        assert tokens == ["第二轮：查到了"]
        # 首轮无 token 不发；仅第二轮 token 前发一次
        assert len(starts) == 1
        assert starts[0] < next(i for i, (e, _) in enumerate(events) if e == "token")

    @pytest.mark.asyncio
    async def test_single_turn_emits_message_start(self):
        """单轮回复（无工具调用）：首个 token 前发一次 message:start"""
        async def single_turn(messages, **kw):
            yield ("token", "你好")
            yield ("done", "")

        events = await self._run(single_turn)

        starts = [i for i, (e, _) in enumerate(events) if e == "message:start"]
        assert len(starts) == 1
        assert starts[0] < next(i for i, (e, _) in enumerate(events) if e == "token")
        assert ("token", "你好") in events
        assert ("done", "") in events

    @pytest.mark.asyncio
    async def test_fallback_emits_message_start(self):
        """fallback 降级回复：统一发 message:start（#22 fallback 路径）"""
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
        starts = [i for i, (e, _) in enumerate(events) if e == "message:start"]

        # 两轮文字都完整产出（fallback 文案是独立 message）
        assert tokens == ["第一轮：我来查一下", fallback_msg]
        # 两条回复（正常 + fallback）→ 两个 message:start，各在对应 token 前
        assert len(starts) == 2
        assert starts[0] < next(i for i, (e, _) in enumerate(events) if e == "token")
        assert starts[1] < next(
            i for i, (e, data) in enumerate(events)
            if e == "token" and data == fallback_msg
        )

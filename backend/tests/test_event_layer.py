"""事件层单测：白名单过滤（TECH_SPEC §4.2/§4.3）

2026-09-04：format_sse 帧化函数随 SSE 通道退役删除（v2 chat 统一 WS，
帧化由 WS 传输层 JSON 直发）——本文件只保留白名单过滤测试。
"""

from app.ai.event_layer import should_forward


def _ev(etype: str, data: dict | None = None) -> dict:
    return {"type": etype, "data": data or {}}


class TestShouldForward:
    """白名单：5 事件放行，其余过滤"""

    def test_text_delta_forward(self):
        assert should_forward(_ev("assistant/chunk", {"chunk": {"type": "text-delta", "text": "你好"}}))

    def test_reasoning_delta_filtered(self):
        """thinking 不转发（§4.2）"""
        assert not should_forward(_ev("assistant/chunk", {"chunk": {"type": "reasoning-delta", "text": "..."}}))

    def test_chunk_other_subtypes_filtered(self):
        for ctype in ("block-start", "block-end", "usage", "finish"):
            assert not should_forward(_ev("assistant/chunk", {"chunk": {"type": ctype}})), ctype

    def test_step_start_forward(self):
        assert should_forward(_ev("step/start", {"turn": 1, "step": 1}))

    def test_user_message_forward(self):
        assert should_forward(_ev("user/message", {"content": "你好"}))

    def test_turn_boundaries_forward(self):
        assert should_forward(_ev("turn/start", {"turn": 1}))
        assert should_forward(_ev("turn/end", {"turn": 1, "reason": {"kind": "completed"}}))

    def test_tool_events_filtered(self):
        """工具过程不转发（前端不关心，§4.2）"""
        assert not should_forward(_ev("tool/call", {"name": "mcp__lanyuan__search_history"}))
        assert not should_forward(_ev("tool/result", {"message": "ok"}))

    def test_internal_events_filtered(self):
        for etype in ("session/title", "request/header", "agent/inbox/spliced"):
            assert not should_forward(_ev(etype, {})), etype

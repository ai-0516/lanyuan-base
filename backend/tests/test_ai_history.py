"""v2 历史列表测试（TECH_SPEC §10.4：历史列表数据源 = DSH session 日志派生）

覆盖两层：
1. app.ai.history.project_messages 纯函数——events → 消息序列投影规则
   （user/message → 用户气泡；step/start + text-delta → assistant 气泡；
   纯工具步骤/空回复丢弃；reasoning-delta 不投影）
2. GET /api/v2/ai/session/{id}/messages——归属校验 403、分页契约
   （before_seq 游标 + 倒序 + has_more）。events 查询被 mock
   （SQLite 测试库无 v2 events 表，MySQL 结构；真实路径由
   scripts/verify_v2_m3.py 集成验证覆盖）
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.ai.history import project_messages
from app.core.database import get_db
from app.main import app


# ── 工具：构造事件 ──────────────────────────────────────────────

def _ev(seq: int, etype: str, data: dict | str | None = None, time: int | None = None) -> dict:
    return {"seq": seq, "type": etype, "data": data or {}, "time": time or seq * 1000}


def _delta(seq: int, text: str) -> dict:
    return _ev(seq, "assistant/chunk", {"chunk": {"type": "text-delta", "text": text}})


# ── 1. 投影纯函数 ──────────────────────────────────────────────

class TestProjectMessages:
    def test_basic_conversation(self):
        """一轮完整对话：user/message → step/start → text-delta → turn/end"""
        events = [
            _ev(1, "user/message", {"content": "你好"}),
            _ev(2, "turn/start", {"turn": 1}),
            _ev(3, "step/start", {"turn": 1, "step": 1}),
            _delta(4, "你好"),
            _delta(5, "！"),
            _ev(6, "turn/end", {"turn": 1, "reason": {"kind": "completed"}}),
        ]
        msgs = project_messages(events)
        assert msgs == [
            {"role": "user", "content": "你好", "seq": 1, "time": 1000},
            {"role": "assistant", "content": "你好！", "seq": 3, "time": 3000},
        ]

    def test_tool_only_step_dropped(self):
        """纯工具步骤（step/start 后无 text-delta）→ 气泡丢弃（§10.1）"""
        events = [
            _ev(1, "user/message", {"content": "查一下资料"}),
            _ev(2, "turn/start", {"turn": 1}),
            _ev(3, "step/start", {"turn": 1, "step": 1}),
            _ev(4, "assistant/chunk", {"chunk": {"type": "block-start"}}),  # 非 text-delta
            _ev(5, "tool/call", {"name": "mcp__lanyuan__search_history"}),
            _ev(6, "tool/result", {"message": "ok"}),
            _ev(7, "step/end", {"turn": 1, "step": 1}),
            _ev(8, "step/start", {"turn": 1, "step": 2}),
            _delta(9, "找到了："),
            _delta(10, "物业电话 010-8888-6666"),
            _ev(11, "turn/end", {"turn": 1, "reason": {"kind": "completed"}}),
        ]
        msgs = project_messages(events)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1] == {"role": "assistant", "content": "找到了：物业电话 010-8888-6666", "seq": 8, "time": 8000}

    def test_empty_final_bubble_dropped(self):
        """turn/end 时最终气泡仍空 → 丢弃（§10.1）"""
        events = [
            _ev(1, "user/message", {"content": "在吗"}),
            _ev(2, "turn/start", {"turn": 1}),
            _ev(3, "step/start", {"turn": 1, "step": 1}),
            _ev(4, "tool/call", {"name": "mcp__lanyuan__get_my_profile"}),
            _ev(5, "tool/result", {"message": "ok"}),
            _ev(6, "turn/end", {"turn": 1, "reason": {"kind": "completed"}}),
        ]
        msgs = project_messages(events)
        assert msgs == [{"role": "user", "content": "在吗", "seq": 1, "time": 1000}]

    def test_multiple_turns(self):
        """多轮对话：多 user/message + 多 assistant 段按序拼接"""
        events = [
            _ev(1, "user/message", {"content": "第一轮"}),
            _ev(2, "step/start", {"turn": 1, "step": 1}),
            _delta(3, "回复一"),
            _ev(4, "turn/end", {"turn": 1, "reason": {"kind": "completed"}}),
            _ev(5, "user/message", {"content": "第二轮"}),
            _ev(6, "step/start", {"turn": 2, "step": 1}),
            _delta(7, "回复二"),
            _ev(8, "turn/end", {"turn": 2, "reason": {"kind": "completed"}}),
        ]
        msgs = project_messages(events)
        assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
        assert [m["content"] for m in msgs] == ["第一轮", "回复一", "第二轮", "回复二"]

    def test_reasoning_delta_not_projected(self):
        """reasoning-delta 不投影（thinking 不展示，§4.2）"""
        events = [
            _ev(1, "step/start", {"turn": 1, "step": 1}),
            _ev(2, "assistant/chunk", {"chunk": {"type": "reasoning-delta", "text": "思考中"}}),
            _delta(3, "正式回复"),
            _ev(4, "turn/end", {"turn": 1, "reason": {"kind": "completed"}}),
        ]
        msgs = project_messages(events)
        assert msgs == [{"role": "assistant", "content": "正式回复", "seq": 1, "time": 1000}]

    def test_unfinished_stream_flushed(self):
        """无 turn/end 的流（异常中断残留）：兜底收尾"""
        events = [
            _ev(1, "user/message", {"content": "hi"}),
            _ev(2, "step/start", {"turn": 1, "step": 1}),
            _delta(3, "部分回复"),
        ]
        msgs = project_messages(events)
        assert msgs == [
            {"role": "user", "content": "hi", "seq": 1, "time": 1000},
            {"role": "assistant", "content": "部分回复", "seq": 2, "time": 2000},
        ]

    def test_user_message_content_blocks(self):
        """真实 DSH 形态：user/message content = content block 数组（§8.2 events.data）"""
        events = [
            _ev(1, "user/message", {"content": [{"type": "text", "text": "你好"}, {"type": "text", "text": "兰园"}]}),
            _ev(2, "step/start", {"turn": 1, "step": 1}),
            _delta(3, "嗨"),
            _ev(4, "turn/end", {"turn": 1, "reason": {"kind": "completed"}}),
        ]
        msgs = project_messages(events)
        assert msgs[0]["content"] == "你好兰园"
        assert msgs[1]["content"] == "嗨"

    def test_data_as_json_string(self):
        """text() 原生查询下 events.data 是 str（SQLite/MySQL 驱动均不反序列化 JSON 列）"""
        events = [
            _ev(1, "user/message", json.dumps({"content": "字符串 data"}, ensure_ascii=False)),
            _ev(2, "step/start", json.dumps({"turn": 1, "step": 1})),
            _ev(3, "assistant/chunk", json.dumps({"chunk": {"type": "text-delta", "text": "回复"}})),
            _ev(4, "turn/end", json.dumps({"turn": 1, "reason": {"kind": "completed"}})),
        ]
        msgs = project_messages(events)
        assert msgs[0]["content"] == "字符串 data"
        assert msgs[1]["content"] == "回复"

    def test_bad_data_json_ignored(self):
        """data 解析失败（异常数据）按空 dict 处理，不抛异常（空 content 消息）"""
        events = [
            _ev(1, "user/message", "{broken json"),
            _ev(2, "turn/end", {"turn": 1, "reason": {"kind": "completed"}}),
        ]
        msgs = project_messages(events)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user" and msgs[0]["content"] == ""

    def test_empty_events(self):
        assert project_messages([]) == []


# ── 2. API：GET /api/v2/ai/session/{id}/messages ──────────────

@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def override_auth():
    from app.api.deps import get_current_user

    app.dependency_overrides[get_current_user] = lambda: 7
    yield
    app.dependency_overrides.pop(get_current_user, None)


class FakeResult:
    """fake (await db.execute()).mappings().all() 的返回值（dict 行，[dict(r)] 兼容）"""

    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeDB:
    """fake get_db 依赖：模拟 turn 级分页的两条 SQL（SQLite 测试库无 v2 events 表，
    MySQL 结构由 verify 集成验证）

    - turn/start 查询（含 "turn/start"）：返回 (seq,) DESC 列表
    - 事件窗口查询：seq >= earliest 且 seq < cursor 的事件（升序）
    """

    def __init__(self, events):
        self._events = list(events)
        self._turn_starts = sorted(e["seq"] for e in events if e["type"] == "turn/start")
        self.captured: dict = {}

    async def execute(self, stmt, params):
        self.captured.update(params)
        if "turn/start" in str(stmt):
            before = params.get("cursor")
            rows = self._turn_starts if before is None else [s for s in self._turn_starts if s < before]
            rows = rows[::-1]  # ORDER BY seq DESC
            return FakeResult([(s,) for s in rows[: params.get("limit", len(rows))]])
        earliest = params["earliest"]
        before = params.get("cursor")
        rows = [
            e for e in self._events
            if e["seq"] >= earliest and (before is None or e["seq"] < before)
        ]
        return FakeResult(rows)


def _sample_events() -> list[dict]:
    """两个 turn 的事件序列（真实 DSH 事件序：turn/start → step/start → user/message）"""
    return [
        # turn 1
        {"seq": 10, "type": "turn/start", "data": {"turn": 1}, "time": 1000},
        {"seq": 11, "type": "step/start", "data": {"turn": 1, "step": 1}, "time": 2000},
        {"seq": 12, "type": "user/message", "data": {"content": "你好"}, "time": 3000},
        {"seq": 13, "type": "assistant/chunk",
         "data": {"chunk": {"type": "text-delta", "text": "你"}}, "time": 4000},
        {"seq": 14, "type": "assistant/chunk",
         "data": {"chunk": {"type": "text-delta", "text": "好！"}}, "time": 5000},
        {"seq": 15, "type": "turn/end",
         "data": {"turn": 1, "reason": {"kind": "completed"}}, "time": 6000},
        # turn 2
        {"seq": 16, "type": "turn/start", "data": {"turn": 2}, "time": 7000},
        {"seq": 17, "type": "step/start", "data": {"turn": 2, "step": 1}, "time": 8000},
        {"seq": 18, "type": "user/message", "data": {"content": "再问一个"}, "time": 9000},
        {"seq": 19, "type": "assistant/chunk",
         "data": {"chunk": {"type": "text-delta", "text": "回答"}}, "time": 10000},
        {"seq": 20, "type": "turn/end",
         "data": {"turn": 2, "reason": {"kind": "completed"}}, "time": 11000},
    ]


class TestV2HistoryApi:
    async def test_owner_mismatch_403(self, client, override_auth):
        with patch("app.api.v2.ai.get_session_owner", return_value=42):
            resp = await client.get("/api/v2/ai/session/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/messages")
        assert resp.status_code == 403

    async def test_owner_missing_403(self, client, override_auth):
        with patch("app.api.v2.ai.get_session_owner", return_value=None):
            resp = await client.get("/api/v2/ai/session/bbbbbbbb-cccc-4ddd-8eee-ffffffffffff/messages")
        assert resp.status_code == 403

    async def test_returns_projected_messages_desc(self, client, override_auth):
        """owner 通过 → 查询 events → 投影消息倒序返回（最新在前）+ cursor"""
        db = FakeDB(_sample_events())
        with patch("app.api.v2.ai.get_session_owner", return_value=7):
            app.dependency_overrides[get_db] = lambda: db
            try:
                resp = await client.get("/api/v2/ai/session/cccccccc-dddd-4eee-8fff-000000000000/messages")
            finally:
                app.dependency_overrides.pop(get_db, None)
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_more"] is False
        assert body["cursor"] == 10  # 本页最旧 turn/start 的 seq（游标供下次 before_seq）
        # 倒序（最新在前）：第二条 assistant → 第二条 user → 第一条 assistant → 第一条 user
        assert [m["role"] for m in body["messages"]] == ["assistant", "user", "assistant", "user"]
        assert body["messages"][0]["content"] == "回答"
        assert body["messages"][1]["content"] == "再问一个"
        assert body["messages"][2]["content"] == "你好！"
        assert body["messages"][3]["content"] == "你好"

    async def test_pagination_turn_cursor_roundtrip(self, client, override_auth):
        """turn 级分页：cursor 往返不漏不重——同轮 user+assistant 永远成对出现

        关键场景：turn2 的 step/start seq(7) < user/message seq(8)（真实 DSH 事件序），
        按消息/事件 seq 分页会把一轮拆页；turn 级游标保证成对。
        """
        events = [
            {"seq": 1, "type": "turn/start", "data": {"turn": 1}, "time": 1000},
            {"seq": 2, "type": "step/start", "data": {"turn": 1, "step": 1}, "time": 2000},
            {"seq": 3, "type": "user/message", "data": {"content": "第一问"}, "time": 3000},
            {"seq": 4, "type": "assistant/chunk",
             "data": {"chunk": {"type": "text-delta", "text": "答一"}}, "time": 4000},
            {"seq": 5, "type": "turn/end",
             "data": {"turn": 1, "reason": {"kind": "completed"}}, "time": 5000},
            {"seq": 6, "type": "turn/start", "data": {"turn": 2}, "time": 6000},
            {"seq": 7, "type": "step/start", "data": {"turn": 2, "step": 1}, "time": 7000},  # seq < user seq
            {"seq": 8, "type": "user/message", "data": {"content": "第二问"}, "time": 8000},
            {"seq": 9, "type": "assistant/chunk",
             "data": {"chunk": {"type": "text-delta", "text": "答二"}}, "time": 9000},
            {"seq": 10, "type": "turn/end",
             "data": {"turn": 2, "reason": {"kind": "completed"}}, "time": 10000},
        ]
        db = FakeDB(events)
        with patch("app.api.v2.ai.get_session_owner", return_value=7):
            app.dependency_overrides[get_db] = lambda: db
            try:
                # 第 1 页：limit=1（turn）→ turn 2 完整（答二 + 第二问 成对）
                r1 = await client.get(
                    "/api/v2/ai/session/cccccccc-dddd-4eee-8fff-000000000000/messages",
                    params={"limit": 1},
                )
                b1 = r1.json()
                assert [m["content"] for m in b1["messages"]] == ["答二", "第二问"]
                assert b1["cursor"] == 6
                assert b1["has_more"] is True
                # 第 2 页：before_seq=cursor → turn 1 完整（答一 + 第一问 成对）
                r2 = await client.get(
                    "/api/v2/ai/session/cccccccc-dddd-4eee-8fff-000000000000/messages",
                    params={"before_seq": b1["cursor"], "limit": 1},
                )
                b2 = r2.json()
                assert [m["content"] for m in b2["messages"]] == ["答一", "第一问"]
                assert b2["cursor"] == 1
                assert b2["has_more"] is False
                # 到底后再翻：空
                r3 = await client.get(
                    "/api/v2/ai/session/cccccccc-dddd-4eee-8fff-000000000000/messages",
                    params={"before_seq": b2["cursor"], "limit": 1},
                )
                b3 = r3.json()
                assert b3["messages"] == []
                assert b3["has_more"] is False
            finally:
                app.dependency_overrides.pop(get_db, None)

    async def test_requires_auth(self, client):
        resp = await client.get("/api/v2/ai/session/cccccccc-dddd-4eee-8fff-000000000000/messages")
        assert resp.status_code == 401

    async def test_limit_clamped(self, client, override_auth):
        """limit clamp 边界（PR #98 review 建议）：0/负 → 1，>50 → 50。

        FakeDB.captured 记录 SQL 参数：turn/start 查询的 LIMIT 参数 =
        clamp(limit)+1（多取 1 条判断 has_more）。
        """
        db = FakeDB(_sample_events())
        with patch("app.api.v2.ai.get_session_owner", return_value=7):
            app.dependency_overrides[get_db] = lambda: db
            try:
                for raw, expected_sql_limit in [(0, 2), (-5, 2), (51, 51)]:
                    db.captured = {}
                    resp = await client.get(
                        "/api/v2/ai/session/cccccccc-dddd-4eee-8fff-000000000000/messages",
                        params={"limit": raw},
                    )
                    assert resp.status_code == 200
                    # clamp(0)=1→SQL LIMIT 2；clamp(-5)=1→2；clamp(51)=50→51
                    assert db.captured["limit"] == expected_sql_limit, (
                        f"limit={raw} 未按 clamp 语义传递，SQL LIMIT 参数={db.captured['limit']}"
                    )
            finally:
                app.dependency_overrides.pop(get_db, None)

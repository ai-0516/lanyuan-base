"""v2 M3 内部身份端点单测（TECH_SPEC §6.3：GET /api/v2/internal/sessions/{id}/owner）

覆盖：
- X-Lanyuan-Internal-Token 鉴权：缺失 / 错误 → 401（fail-closed）
- 正确 token → 200 {owner_user_id}
- owner 映射不存在（get_session_owner 返回 None）→ 404

owner 查询的 MySQL 读写不在此测（backend 测试库是 SQLite，v2 会话表是
MySQL 结构）——mock 掉 get_session_owner；真实 MySQL 路径由
scripts/verify_v2_m3.py 集成验证覆盖。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.core.security import get_mcp_token


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def _no_db():
    """本组测试不触碰数据库（owner 查询被 mock）。"""
    yield


class TestSessionOwnerInternal:
    async def test_missing_token_401(self, client: AsyncClient):
        resp = await client.get("/api/v2/internal/sessions/v2-abc/owner")
        assert resp.status_code == 401
        assert resp.json()["code"] == 40110

    async def test_wrong_token_401(self, client: AsyncClient):
        resp = await client.get(
            "/api/v2/internal/sessions/v2-abc/owner",
            headers={"X-Lanyuan-Internal-Token": "wrong-token"},
        )
        assert resp.status_code == 401

    async def test_valid_token_returns_owner(self, client: AsyncClient):
        with patch("app.api.internal.get_session_owner", return_value=42):
            resp = await client.get(
                "/api/v2/internal/sessions/v2-abc/owner",
                headers={"X-Lanyuan-Internal-Token": get_mcp_token()},
            )
        assert resp.status_code == 200
        assert resp.json() == {"owner_user_id": 42}

    async def test_no_mapping_404(self, client: AsyncClient):
        with patch("app.api.internal.get_session_owner", return_value=None):
            resp = await client.get(
                "/api/v2/internal/sessions/v2-ghost/owner",
                headers={"X-Lanyuan-Internal-Token": get_mcp_token()},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == 40410


class TestV2SessionEndpoint:
    """POST /api/v2/ai/session（v2 会话统一创建点，PR #97 review 定案）。

    get_or_create_session_v2 的 MySQL 读写不在此测（SQLite 测试库无 v2 表）——
    mock 掉；真实路径由 scripts/verify_v2_m3.py 集成验证覆盖。
    """

    @pytest.fixture
    def override_auth(self):
        from app.api.deps import get_current_user

        app.dependency_overrides[get_current_user] = lambda: 7
        yield
        app.dependency_overrides.pop(get_current_user, None)

    async def test_create_session_returns_id(self, client: AsyncClient, override_auth):
        with patch("app.api.v2.ai.get_or_create_session_v2", return_value="11111111-2222-4333-8444-555555555555"):
            resp = await client.post("/api/v2/ai/session")
        assert resp.status_code == 200
        assert resp.json() == {"session_id": "11111111-2222-4333-8444-555555555555"}

    async def test_requires_auth(self, client: AsyncClient):
        resp = await client.post("/api/v2/ai/session")
        assert resp.status_code == 401


class TestV2ChatOwnership:
    """WebSocket /api/v2/ai/chat/ws 归属校验（2026-09-04 路线2：SSE POST /chat
    退役 → WS 统一通道；owner 校验语义不变——调用者必须持有 session owner
    身份，否则可 resume 他人会话上下文 + 工具以他人身份执行）。

    owner 查询的 MySQL 读写不在此测（SQLite 测试库无 v2 表）——mock 掉
    get_session_owner；_chat_events 同样 mock（避免真跑 DSH runtime）。
    真实路径由 scripts/verify_v2_m3.py 集成验证覆盖。

    鉴权走首帧 token（decode_access_token，非 HTTP 依赖）→ 用真 JWT 测。
    """

    SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

    @pytest.fixture
    def token_7(self):
        from app.core.security import create_access_token

        return create_access_token(7)

    @pytest.fixture(autouse=True)
    def _no_runtime_prewarm(self):
        """TestClient 跑完整 lifespan 会后台预热 dsh_runtime——测试环境无 DSH
        密钥/进程，patch 掉避免后台任务异常噪音。"""
        from app.ai.dsh_runtime import dsh_runtime

        with patch.object(dsh_runtime, "start", return_value=None):
            yield

    def _ws_frames(self, payload: dict, patch_owner, fake_events=None):
        """连 WS → 发首帧 → 收帧直到连接关闭，返回 (frames, close_code)"""
        from fastapi.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        async def _event_gen(*a, **k):
            # 必须是 async generator：chat_ws 用 async for 消费
            events = fake_events if fake_events is not None else [
                {"type": "turn/end", "data": {"reason": {"kind": "done"}}}
            ]
            for evt in events:
                yield evt

        frames = []
        close_code = None
        with patch("app.api.v2.ai.get_session_owner", return_value=patch_owner), \
                patch("app.api.v2.ai._chat_events", new=_event_gen):
            with TestClient(app) as tc:
                with tc.websocket_connect("/api/v2/ai/chat/ws") as ws:
                    ws.send_json(payload)
                    while True:
                        try:
                            frames.append(ws.receive_json())
                        except WebSocketDisconnect as e:
                            close_code = e.code
                            break
        return frames, close_code

    def test_owner_is_caller_passes(self, token_7):
        """owner == 调用者（7）→ 进入事件流，收到事件帧后服务端正常关闭（1000）"""
        frames, close_code = self._ws_frames(
            {"token": token_7, "session_id": self.SID, "message": "你好"},
            patch_owner=7,
        )
        assert frames == [{"type": "turn/end", "data": {"reason": {"kind": "done"}}}]
        assert close_code == 1000

    def test_owner_is_caller_forwards_events(self, token_7):
        """事件逐帧透传（白名单事件原样转发——step/start、user/message 等）"""
        events = [
            {"type": "turn/start", "data": {}},
            {"type": "user/message", "data": {"content": [{"type": "text", "text": "你好"}]}},
            {"type": "turn/end", "data": {"reason": {"kind": "done"}}},
        ]
        frames, close_code = self._ws_frames(
            {"token": token_7, "session_id": self.SID, "message": "你好"},
            patch_owner=7,
            fake_events=events,
        )
        assert frames == events
        assert close_code == 1000

    def test_invalid_token_4401(self):
        """token 无效 → error 帧 + close 4401（登录过期语义）"""
        frames, close_code = self._ws_frames(
            {"token": "invalid-token", "session_id": self.SID, "message": "你好"},
            patch_owner=7,
        )
        assert frames[0]["type"] == "error"
        assert "登录" in frames[0]["data"]["message"]
        assert close_code == 4401

    def test_owner_mismatch_4403(self, token_7):
        """owner != 调用者（他人 session）→ error 帧 + close 4403（横向越权）"""
        frames, close_code = self._ws_frames(
            {"token": token_7, "session_id": "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff", "message": "你好"},
            patch_owner=42,
        )
        assert frames[0]["type"] == "error"
        assert "无权访问" in frames[0]["data"]["message"]
        assert close_code == 4403

    def test_owner_missing_4403(self, token_7):
        """session 无 owner 映射（绕过统一创建点 / 不存在）→ fail-closed 4403"""
        frames, close_code = self._ws_frames(
            {"token": token_7, "session_id": "cccccccc-dddd-4eee-8fff-000000000000", "message": "你好"},
            patch_owner=None,
        )
        assert frames[0]["type"] == "error"
        assert close_code == 4403

    def test_empty_message_1008(self, token_7):
        """空消息 → error 帧 + close 1008（参数错误）"""
        frames, close_code = self._ws_frames(
            {"token": token_7, "session_id": self.SID, "message": "   "},
            patch_owner=7,
        )
        assert frames[0]["type"] == "error"
        assert "消息不能为空" in frames[0]["data"]["message"]
        assert close_code == 1008


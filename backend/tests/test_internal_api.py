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
    """POST /api/v2/ai/chat 归属校验（PR #97 dev-lead review：调用者必须持有
    session owner 身份，否则可 resume 他人会话上下文 + 工具以他人身份执行）。

    owner 查询的 MySQL 读写不在此测（SQLite 测试库无 v2 表）——mock 掉
    get_session_owner；真实路径由 scripts/verify_v2_m3.py 集成验证覆盖。
    """

    @pytest.fixture
    def override_auth(self):
        from app.api.deps import get_current_user

        app.dependency_overrides[get_current_user] = lambda: 7
        yield
        app.dependency_overrides.pop(get_current_user, None)

    def _post(self, client: AsyncClient, session_id: str):
        return client.post(
            "/api/v2/ai/chat",
            json={"message": "你好", "session_id": session_id},
        )

    async def test_owner_is_caller_passes(self, client: AsyncClient, override_auth):
        """owner == 调用者（7）→ 通过校验，进入 SSE 流（200）。

        _stream_chat 被 mock（归属校验是本测试目标；DSH 真实对话由
        scripts/verify_v2_m3.py 集成验证覆盖，这里避免真跑 DSH runtime）。
        """
        async def fake_stream(*args, **kwargs):
            yield "event: done\ndata: {}\n\n"

        with patch("app.api.v2.ai.get_session_owner", return_value=7), \
                patch("app.api.v2.ai._stream_chat", new=fake_stream):
            resp = await self._post(client, "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

    async def test_owner_mismatch_403(self, client: AsyncClient, override_auth):
        """owner != 调用者（他人 session）→ 403 拒绝（横向越权）。"""
        with patch("app.api.v2.ai.get_session_owner", return_value=42):
            resp = await self._post(client, "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff")
        assert resp.status_code == 403

    async def test_owner_missing_403(self, client: AsyncClient, override_auth):
        """session 无 owner 映射（绕过统一创建点 / 不存在）→ 403 拒绝（fail-closed）。"""
        with patch("app.api.v2.ai.get_session_owner", return_value=None):
            resp = await self._post(client, "cccccccc-dddd-4eee-8fff-000000000000")
        assert resp.status_code == 403

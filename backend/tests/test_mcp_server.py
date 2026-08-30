"""M2 工具桥单测：user_id 注入（§6.3）+ @mcp_tool 全量注册（§6.4b，工具=endpoint）

覆盖：
- _user_id_from_meta：从 callTool `_meta` 提取（Meta 是 pydantic 模型，extra=allow）
- 无 _meta / 无 user_id 字段 → PermissionError（桥层未注入 = 拒绝执行）
- 工具=endpoint：@mcp_tool 直接写在业务 endpoint 上（@tool 旁边叠加，v1/v2 仅限
  /ai/chat，其他 endpoint 不变）——MCP 链路：_meta 身份 → user_id 注入 →
  业务函数 → api_success 解包 → _to_dict → result_formatter 输出（同 v1
  ToolDef.execute）
- Pydantic model 参数展平（同 v1 _flatten_model）：create_post 的 content/images
  展平为独立字段，执行时重建 PostCreate
- 双形态：HTTP 模式走 FastAPI Depends（/api/v1 原路径，endpoint 行为不变）；
  MCP 模式 formatter 删隐私
- schema：业务参数进 MCP schema，注入参数（user_id/db）不暴露
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.main  # noqa: F401  # 触发全部 @mcp_tool 注册（业务文件 import 即注册）
from app.models.user import User
from tools.mcp_server.decorator import _REGISTERED_TOOLS, _user_id_from_meta, mcp

# MCP 工具面 = 19 个业务工具（search_history 不迁移，v2 用 DSH session-query）
ALL_TOOLS = {
    "get_my_profile", "update_my_profile", "get_user_public",
    "list_posts", "create_post", "get_post", "delete_post", "like_post", "unlike_post",
    "list_comments", "create_comment", "delete_comment",
    "list_notifications", "notification_count", "mark_all_read",
    "memory_list", "memory_add", "memory_get", "memory_delete",
}


def _ctx_with_meta(meta):
    """构造 fastmcp Context 的轻量替身（request_context.meta 即 MCP SDK 的 Meta 模型）"""
    return SimpleNamespace(request_context=SimpleNamespace(meta=meta))


class TestUserIdExtraction:
    def test_user_id_from_meta(self):
        # MCP SDK 的 Meta 是 pydantic 模型（extra=allow）：user_id 作为额外属性
        meta = SimpleNamespace(user_id=42)
        assert _user_id_from_meta(_ctx_with_meta(meta)) == 42

    def test_user_id_missing_meta(self):
        with pytest.raises(PermissionError):
            _user_id_from_meta(_ctx_with_meta(None))

    def test_user_id_meta_without_field(self):
        # _meta 存在但无 user_id 字段（如只有 progressToken）
        meta = SimpleNamespace(progressToken="p1")
        with pytest.raises(PermissionError):
            _user_id_from_meta(_ctx_with_meta(meta))

    def test_user_id_string_coerced(self):
        meta = SimpleNamespace(user_id="7")
        assert _user_id_from_meta(_ctx_with_meta(meta)) == 7


def _make_user(user_id: int, nickname: str) -> User:
    """真实 User model 实例（get_my_profile 查询对应用户）"""
    return User(
        id=user_id,
        nickname=nickname,
        community="兰园",
        building="3栋",
        bio="",
        show_building=True,
        show_room=False,
        avatar="data:base64...",  # 隐私字段：formatter 删减
        openid="o_test",
        unionid="u_test",
        unit="5",
        room="601",
    )


class FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows if isinstance(rows, list) else [rows]

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """async_session_factory 替身：execute 按传入对象返回；写操作方法记录调用"""

    def __init__(self, execute_result):
        self._execute_result = execute_result
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt):
        return self._execute_result

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass


class TestGetMyProfile:
    @pytest.mark.asyncio
    async def test_profile_uses_user_id_from_meta(self):
        """user_id 来自 _meta（桥层注入）→ @mcp_tool 注入业务函数 → 查询对应用户
        api_success 解包 + formatter 输出 JSON 字符串（同 v1 ToolDef.execute）"""
        fake = FakeSession(FakeScalarResult([_make_user(42, "桥接用户")]))
        with patch("tools.mcp_server.decorator.async_session_factory", return_value=fake):
            result = await _REGISTERED_TOOLS["get_my_profile"](
                ctx=_ctx_with_meta(SimpleNamespace(user_id=42))
            )

        data = json.loads(result)
        assert data["id"] == 42
        assert data["nickname"] == "桥接用户"
        assert "code" not in result, "api_success 包装应被解包（formatter 收到的是 data）"
        assert fake.committed, "应请求级 commit（对齐 FastAPI get_db 契约）"

    @pytest.mark.asyncio
    async def test_profile_strips_private_fields(self):
        """隐私字段（avatar/openid/unionid/unit/room）由 formatter 删减（v1 同款）"""
        fake = FakeSession(FakeScalarResult([_make_user(42, "桥接用户")]))
        with patch("tools.mcp_server.decorator.async_session_factory", return_value=fake):
            result = await _REGISTERED_TOOLS["get_my_profile"](
                ctx=_ctx_with_meta(SimpleNamespace(user_id=42))
            )

        data = json.loads(result)
        for private in ("avatar", "openid", "unionid", "unit", "room"):
            assert private not in data, f"{private} 不应出现在工具结果中"
        assert data["community"] == "兰园"

    @pytest.mark.asyncio
    async def test_profile_rejects_without_meta(self):
        with pytest.raises(PermissionError):
            await _REGISTERED_TOOLS["get_my_profile"](ctx=_ctx_with_meta(None))

    @pytest.mark.asyncio
    async def test_profile_none_user_returns_null(self):
        """查询无果 = 正常结果（formatter 输出 "null"，不是业务失败）"""
        fake = FakeSession(FakeScalarResult([]))
        with patch("tools.mcp_server.decorator.async_session_factory", return_value=fake):
            result = await _REGISTERED_TOOLS["get_my_profile"](
                ctx=_ctx_with_meta(SimpleNamespace(user_id=999))
            )
        assert result == "null"


class TestModelFlatten:
    """Pydantic model 参数展平（同 v1 _flatten_model）：create_post 的
    content/images 展平为独立字段，执行时重建 PostCreate"""

    @pytest.mark.asyncio
    async def test_schema_flattens_model_fields(self):
        tools = {t.name: t for t in await mcp.list_tools()}
        params = tools["create_post"].parameters
        props = params["properties"]
        assert "data" not in props, "model 参数本身不应暴露（展平为字段）"
        assert "content" in props and props["content"]["type"] == "string"
        assert "images" in props, "images 应展平为独立字段"
        assert params["required"] == ["content"], f"content 必填: {params['required']}"

    @pytest.mark.asyncio
    async def test_execute_rebuilds_model(self):
        """展平字段 → 重建 PostCreate 实例 → 业务函数收到 model"""
        fake = FakeSession(FakeScalarResult([]))
        captured = {}

        async def fake_create_post(db, user_id, data):
            captured["data"] = data
            return {"id": 1, "content": data.content}

        with patch("tools.mcp_server.decorator.async_session_factory", return_value=fake), \
             patch("app.api.v1.posts.post_service.create_post", new=fake_create_post):
            result = await _REGISTERED_TOOLS["create_post"](
                ctx=_ctx_with_meta(SimpleNamespace(user_id=42)),
                content="你好",
                images=["a.jpg"],
            )

        data = captured["data"]
        assert data.content == "你好"
        assert data.images == ["a.jpg"]
        assert fake.committed, "写操作应请求级 commit"

    @pytest.mark.asyncio
    async def test_memory_add_name_registered(self):
        """@mcp_tool(name=...) 与 v1 @tool 同名注册（memory_list 等）"""
        tools = {t.name for t in await mcp.list_tools()}
        assert "memory_add" in tools and "memory_list" in tools
        assert "add_memory" not in tools, "应使用 v1 工具名 memory_add（非函数名）"


class TestHttpEndpoint:
    """双形态验证：@mcp_tool 写在业务 endpoint 上 → HTTP 模式走 FastAPI Depends
    （/api/v1 原路径，endpoint 行为不变——v1/v2 仅限 /ai/chat）"""

    @staticmethod
    def _make_client(user, current_user_id: int = 42, override_auth: bool = True):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.deps import get_current_user, get_db
        from app.api.v1 import profile as v1_profile

        app = FastAPI()
        app.include_router(v1_profile.router, prefix="/api/v1")

        fake = FakeSession(FakeScalarResult([user]) if user else FakeScalarResult([]))

        async def _fake_db():
            yield fake

        if override_auth:
            app.dependency_overrides[get_current_user] = lambda: current_user_id
        app.dependency_overrides[get_db] = _fake_db
        return TestClient(app), fake

    def test_http_me_returns_api_success(self):
        """HTTP GET /api/v1/user/me 行为不变：本人资料全量（含头像，前端需要）"""
        client, _ = self._make_client(_make_user(42, "HTTP 用户"))
        resp = client.get("/api/v1/user/me")

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["nickname"] == "HTTP 用户"
        assert body["data"]["avatar"] == "data:base64...", "HTTP 形态=本人资料，头像应返回（前端展示用）"

    def test_http_me_requires_auth(self):
        """endpoint 带 Depends(get_current_user)：无 Token → 401（FastAPI 原生行为）"""
        client, _ = self._make_client(_make_user(42, "HTTP 用户"), override_auth=False)
        resp = client.get("/api/v1/user/me")
        assert resp.status_code == 401

    def test_http_me_none_user(self):
        """用户不存在 → code=0 + data=null（查询无果是正常结果）"""
        client, _ = self._make_client(None, current_user_id=999)
        resp = client.get("/api/v1/user/me")
        body = resp.json()
        assert body["code"] == 0
        assert body["data"] is None

    def test_original_fn_returned_by_decorator(self):
        """@mcp_tool 返回原函数（无感语义）——endpoint 函数可被 FastAPI 正常使用"""
        from app.api.v1.profile import get_my_profile

        assert callable(get_my_profile)
        assert get_my_profile.__name__ == "get_my_profile"


class TestMcpToolSchema:
    """@mcp_tool 注册（§6.4b）：19 个业务工具 + 注入参数不暴露"""

    @pytest.mark.asyncio
    async def test_tools_list_has_all_business_tools(self):
        tools = {t.name for t in await mcp.list_tools()}
        assert tools == ALL_TOOLS, f"工具面不一致: {sorted(tools)}"

    @pytest.mark.asyncio
    async def test_schema_excludes_injected_params(self):
        tools = {t.name: t for t in await mcp.list_tools()}
        for name in ("get_my_profile", "list_posts", "memory_add"):
            params = tools[name].parameters
            assert "user_id" not in params["properties"], f"{name}: user_id 不应暴露"
            assert "db" not in params["properties"], f"{name}: db 不应暴露"

    @pytest.mark.asyncio
    async def test_registered_tools_match_mcp(self):
        """_REGISTERED_TOOLS 与 mcp.list_tools 一致（注册真源同步）"""
        tools = {t.name for t in await mcp.list_tools()}
        assert set(_REGISTERED_TOOLS) == tools


class TestMountHttp:
    """MCP server 挂载 FastAPI /mcp（§6.2，streamable-http）"""

    def test_app_mounts_mcp(self):
        """app.main 挂载 /mcp 端点（http_app path=/ 修正：mount 前缀不叠加）"""
        from starlette.routing import Mount

        from app.main import app

        mounts = [r for r in app.routes if isinstance(r, Mount)]
        assert any(m.path == "/mcp" for m in mounts), "/mcp 未挂载"

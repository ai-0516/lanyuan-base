"""M2 工具桥单测：user_id 注入（§6.3）+ @mcp_tool 原生注册（§6.4b）

覆盖：
- _user_id_from_meta：从 callTool `_meta` 提取（Meta 是 pydantic 模型，extra=allow）
- 无 _meta / 无 user_id 字段 → PermissionError（桥层未注入 = 拒绝执行）
- get_my_profile（@mcp_tool）：_meta 身份 → user_id 注入 → 业务函数执行 → 结构化返回
  （隐私字段不返回；写操作请求级 commit——对齐 get_db 契约）
- schema：业务参数进 MCP schema，注入参数（user_id/db）不暴露
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.user import User
import tools.mcp_server.tools  # noqa: F401  # 触发 @mcp_tool 注册（get_my_profile）
from tools.mcp_server.decorator import _REGISTERED_TOOLS, _user_id_from_meta, mcp


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
        avatar="data:base64...",  # 隐私字段：工具不返回
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
        """user_id 来自 _meta（桥层注入）→ @mcp_tool 注入业务函数 → 查询对应用户"""
        fake = FakeSession(FakeScalarResult([_make_user(42, "桥接用户")]))
        with patch("tools.mcp_server.decorator.async_session_factory", return_value=fake):
            result = await _REGISTERED_TOOLS["get_my_profile"](
                ctx=_ctx_with_meta(SimpleNamespace(user_id=42))
            )

        assert result["id"] == 42
        assert result["nickname"] == "桥接用户"
        assert fake.committed, "应请求级 commit（对齐 FastAPI get_db 契约）"

    @pytest.mark.asyncio
    async def test_profile_strips_private_fields(self):
        """隐私字段（avatar/openid/unionid/unit/room）由工具自身控制不返回"""
        fake = FakeSession(FakeScalarResult([_make_user(42, "桥接用户")]))
        with patch("tools.mcp_server.decorator.async_session_factory", return_value=fake):
            result = await _REGISTERED_TOOLS["get_my_profile"](
                ctx=_ctx_with_meta(SimpleNamespace(user_id=42))
            )

        for private in ("avatar", "openid", "unionid", "unit", "room"):
            assert private not in result, f"{private} 不应出现在工具结果中"
        assert result["community"] == "兰园"

    @pytest.mark.asyncio
    async def test_profile_rejects_without_meta(self):
        with pytest.raises(PermissionError):
            await _REGISTERED_TOOLS["get_my_profile"](ctx=_ctx_with_meta(None))

    @pytest.mark.asyncio
    async def test_original_fn_returned_by_decorator(self):
        """@mcp_tool 返回原函数（无感语义）——工具函数可被其他消费方直接调用"""
        from tools.mcp_server.tools import get_my_profile

        assert callable(get_my_profile)
        assert get_my_profile.__name__ == "get_my_profile"


class TestMcpToolSchema:
    """@mcp_tool 注册（§6.4b）：业务参数进 schema，注入参数不暴露"""

    @pytest.mark.asyncio
    async def test_tools_list_has_only_registered_tools(self):
        tools = {t.name: t for t in await mcp.list_tools()}
        assert set(tools) == {"get_my_profile"}, f"应只有 @mcp_tool 注册的工具: {list(tools)}"

    @pytest.mark.asyncio
    async def test_schema_excludes_injected_params(self):
        tools = {t.name: t for t in await mcp.list_tools()}
        params = tools["get_my_profile"].parameters
        assert "user_id" not in params["properties"], "注入参数 user_id 不应暴露"
        assert "db" not in params["properties"], "注入参数 db 不应暴露"
        assert params.get("required", []) == [], "get_my_profile 无业务参数"

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

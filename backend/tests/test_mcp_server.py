"""M2 工具桥单测：user_id 注入（§6.3）+ MCP server 自动注册 v1 @tool（§6.4）

覆盖：
- _user_id：从 callTool `_meta` 提取（Meta 是 pydantic 模型，extra=allow）
- 无 _meta / 无 user_id 字段 → PermissionError（桥层未注入 = 拒绝执行）
- 工具自动注册：MCP schema 与 v1 ToolDef schema 全量一致（无 user_id/db）
- get_my_profile / search_history / update_my_profile：_meta 身份 → v1 复用链
  （ToolDef.execute 注入 + formatter 删减 + 请求级 commit）
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.user import User
from tools.mcp_server.main import _MCP_TOOLS, _user_id, mcp


def _ctx_with_meta(meta):
    """构造 fastmcp Context 的轻量替身（request_context.meta 即 MCP SDK 的 Meta 模型）"""
    return SimpleNamespace(request_context=SimpleNamespace(meta=meta))


class TestUserIdExtraction:
    def test_user_id_from_meta(self):
        # MCP SDK 的 Meta 是 pydantic 模型（extra=allow）：user_id 作为额外属性
        meta = SimpleNamespace(user_id=42)
        assert _user_id(_ctx_with_meta(meta)) == 42

    def test_user_id_missing_meta(self):
        with pytest.raises(PermissionError):
            _user_id(_ctx_with_meta(None))

    def test_user_id_meta_without_field(self):
        # _meta 存在但无 user_id 字段（如只有 progressToken）
        meta = SimpleNamespace(progressToken="p1")
        with pytest.raises(PermissionError):
            _user_id(_ctx_with_meta(meta))

    def test_user_id_string_coerced(self):
        meta = SimpleNamespace(user_id="7")
        assert _user_id(_ctx_with_meta(meta)) == 7


def _make_user(user_id: int, nickname: str) -> User:
    """真实 User model 实例（SQLAlchemy 分支要求 _sa_instance_state/__table__，v1 formatter 契约）"""
    return User(
        id=user_id,
        nickname=nickname,
        community="兰园",
        building="3栋",
        bio="",
        show_building=True,
        show_room=False,
        avatar="data:base64...",  # 隐私字段：formatter 必须删减（#69）
        openid="o_test",
        unionid="u_test",
        unit="5",
        room="601",
    )


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class FakeScalarResult:
    """execute 替身：rows 为 list，统一从 rows 取 scalar_one_or_none / scalars"""

    def __init__(self, rows):
        self._rows = rows if isinstance(rows, list) else [rows]

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return _FakeScalars(self._rows)


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

    async def add(self, obj):
        pass

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass


class TestGetMyProfile:
    @pytest.mark.asyncio
    async def test_profile_uses_user_id_from_meta(self):
        """user_id 来自 _meta（桥层注入），复用 v1 get_my_profile 查询对应用户"""
        fake = FakeSession(FakeScalarResult([_make_user(42, "桥接用户")]))
        with patch("tools.mcp_server.main.async_session_factory", return_value=fake):
            result = await _MCP_TOOLS["get_my_profile"](ctx=_ctx_with_meta(SimpleNamespace(user_id=42)))

        assert result["id"] == 42
        assert result["nickname"] == "桥接用户"

    @pytest.mark.asyncio
    async def test_profile_ignores_model_arguments(self):
        """工具签名无 user_id 参数——即使有人构造带 user_id 的 arguments 也无法注入（工具只收 ctx）"""
        fake = FakeSession(FakeScalarResult([_make_user(9, "本人")]))
        with patch("tools.mcp_server.main.async_session_factory", return_value=fake):
            result = await _MCP_TOOLS["get_my_profile"](ctx=_ctx_with_meta(SimpleNamespace(user_id=9)))
        assert result["id"] == 9

    @pytest.mark.asyncio
    async def test_profile_rejects_without_meta(self):
        with pytest.raises(PermissionError):
            await _MCP_TOOLS["get_my_profile"](ctx=_ctx_with_meta(None))

    @pytest.mark.asyncio
    async def test_profile_strips_private_fields_via_v1_formatter(self):
        """隐私字段删减由 v1 formatter 完成（#69）：avatar/openid/unionid/unit/room 不返回"""
        fake = FakeSession(FakeScalarResult([_make_user(42, "桥接用户")]))
        with patch("tools.mcp_server.main.async_session_factory", return_value=fake):
            result = await _MCP_TOOLS["get_my_profile"](ctx=_ctx_with_meta(SimpleNamespace(user_id=42)))

        for private in ("avatar", "openid", "unionid", "unit", "room"):
            assert private not in result, f"{private} 不应出现在工具结果中"
        assert result["community"] == "兰园"


class TestSearchHistory:
    @pytest.mark.asyncio
    async def test_search_history_via_v1(self):
        """search_history 复用 v1：_meta 注入 user_id → v1 查询（空结果）→ 结构化返回"""
        fake = FakeSession(FakeScalarResult([]))
        with patch("tools.mcp_server.main.async_session_factory", return_value=fake):
            result = await _MCP_TOOLS["search_history"](
                query="地暖", ctx=_ctx_with_meta(SimpleNamespace(user_id=42))
            )

        assert result == {"results": [], "total": 0}

    @pytest.mark.asyncio
    async def test_search_history_rejects_without_meta(self):
        with pytest.raises(PermissionError):
            await _MCP_TOOLS["search_history"](query="地暖", ctx=_ctx_with_meta(None))


class TestUpdateMyProfile:
    @pytest.mark.asyncio
    async def test_update_model_flattened(self):
        """Pydantic model 参数展平（对齐 v1 _flatten_model）：字段直接进 schema，model 参数不暴露"""
        tools = {t.name: t for t in await mcp.list_tools()}
        p = tools["update_my_profile"].parameters
        assert {"nickname", "avatar", "bio", "show_room"} <= set(p["properties"]), "model 字段未展平"
        assert "data" not in p["properties"], "model 参数不应暴露"
        assert "db" not in p["properties"], "Depends 参数不应暴露"
        assert p.get("required", []) == [], "update 全字段可选（UserUpdate 全默认 None）"

    @pytest.mark.asyncio
    async def test_update_executes_via_v1_with_commit(self):
        """写操作链：_meta 身份 → v1 update 执行 → 请求级 commit（对齐 get_db）"""
        fake = FakeSession(FakeScalarResult([_make_user(42, "老昵称")]))
        with patch("tools.mcp_server.main.async_session_factory", return_value=fake):
            result = await _MCP_TOOLS["update_my_profile"](
                nickname="新昵称", ctx=_ctx_with_meta(SimpleNamespace(user_id=42))
            )

        assert result["nickname"] == "新昵称", "v1 formatter 应输出更新后资料"
        assert fake.committed, "写操作应请求级 commit（对齐 FastAPI get_db 契约）"


class TestV1ReuseSchema:
    """自动注册（§6.4）：MCP tools/list 与 v1 ToolDef schema 全量一致，无身份参数"""

    @pytest.mark.asyncio
    async def test_mcp_tools_list_matches_v1_schema(self):
        from app.harness.tool_registry import registry

        v1_by_name = {td.name: td for td in registry.all}
        tools = {t.name: t for t in await mcp.list_tools()}
        assert tools, "MCP 未注册任何工具"
        for mcp_name, mcp_tool in tools.items():
            v1_td = v1_by_name.get(mcp_name)
            assert v1_td is not None, f"MCP 工具无 v1 来源: {mcp_name}"
            v1_params = v1_td.schema["function"]["parameters"]
            mcp_params = mcp_tool.parameters
            assert set(mcp_params["properties"]) == set(v1_params["properties"]), f"{mcp_name} 参数不一致"
            assert mcp_params.get("required", []) == v1_params.get("required", []), f"{mcp_name} required 不一致"
            # Depends 注入参数不暴露（身份/db 由编排层注入，v1 schema 里没有的 MCP 也没有）
            assert "db" not in mcp_params["properties"], f"{mcp_name} 暴露 db 参数"
            assert "current_user_id" not in mcp_params["properties"], f"{mcp_name} 暴露注入身份参数"


class TestMountHttp:
    """MCP server 挂载 FastAPI /mcp（§6.2，streamable-http）"""

    def test_app_mounts_mcp(self):
        """app.main 挂载 /mcp 端点（http_app path=/ 修正：mount 前缀不叠加）"""
        from starlette.routing import Mount

        from app.main import app

        mounts = [r for r in app.routes if isinstance(r, Mount)]
        assert any(m.path == "/mcp" for m in mounts), "/mcp 未挂载"

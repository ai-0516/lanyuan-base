"""M2 工具桥单测：user_id 注入（§6.3）+ MCP server 工具（复用 v1 @tool）

覆盖：
- _user_id：从 callTool `_meta` 提取（Meta 是 pydantic 模型，extra=allow）
- 无 _meta / 无 user_id 字段 → PermissionError（桥层未注入 = 拒绝执行）
- get_profile / search_history：user_id 来自 _meta（非模型参数），复用 v1 @tool
  （ToolDef.execute 注入 db/user_id + formatter 删减，§6.4 业务实现单份）
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.user import User
from tools.mcp_server.main import _user_id, get_profile, search_history


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
    """async_session_factory 替身：execute 按传入对象返回"""

    def __init__(self, execute_result):
        self._execute_result = execute_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt):
        return self._execute_result


class TestGetProfile:
    @pytest.mark.asyncio
    async def test_profile_uses_user_id_from_meta(self):
        """user_id 来自 _meta（桥层注入），复用 v1 get_my_profile 查询对应用户"""
        fake = FakeSession(FakeScalarResult([_make_user(42, "桥接用户")]))
        with patch("tools.mcp_server.main.async_session_factory", return_value=fake):
            result = await get_profile(ctx=_ctx_with_meta(SimpleNamespace(user_id=42)))

        assert result["id"] == 42
        assert result["nickname"] == "桥接用户"

    @pytest.mark.asyncio
    async def test_profile_ignores_model_arguments(self):
        """工具签名无 user_id 参数——即使有人构造带 user_id 的 arguments 也无法注入（get_profile 只收 ctx）"""
        fake = FakeSession(FakeScalarResult([_make_user(9, "本人")]))
        with patch("tools.mcp_server.main.async_session_factory", return_value=fake):
            result = await get_profile(ctx=_ctx_with_meta(SimpleNamespace(user_id=9)))
        assert result["id"] == 9

    @pytest.mark.asyncio
    async def test_profile_rejects_without_meta(self):
        with pytest.raises(PermissionError):
            await get_profile(ctx=_ctx_with_meta(None))

    @pytest.mark.asyncio
    async def test_profile_strips_private_fields_via_v1_formatter(self):
        """隐私字段删减由 v1 formatter 完成（#69）：avatar/openid/unionid/unit/room 不返回"""
        fake = FakeSession(FakeScalarResult([_make_user(42, "桥接用户")]))
        with patch("tools.mcp_server.main.async_session_factory", return_value=fake):
            result = await get_profile(ctx=_ctx_with_meta(SimpleNamespace(user_id=42)))

        for private in ("avatar", "openid", "unionid", "unit", "room"):
            assert private not in result, f"{private} 不应出现在工具结果中"
        assert result["community"] == "兰园"


class TestSearchHistory:
    @pytest.mark.asyncio
    async def test_search_history_via_v1(self):
        """search_history 复用 v1：_meta 注入 user_id → v1 查询（空结果）→ 结构化返回"""
        fake = FakeSession(FakeScalarResult([]))
        with patch("tools.mcp_server.main.async_session_factory", return_value=fake):
            result = await search_history(
                "地暖", ctx=_ctx_with_meta(SimpleNamespace(user_id=42))
            )

        assert result == {"results": [], "total": 0}

    @pytest.mark.asyncio
    async def test_search_history_rejects_without_meta(self):
        with pytest.raises(PermissionError):
            await search_history("地暖", ctx=_ctx_with_meta(None))

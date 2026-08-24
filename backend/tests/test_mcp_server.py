"""M2 工具桥单测：user_id 注入（§6.3）+ MCP server 工具函数

覆盖：
- _user_id：从 callTool `_meta` 提取（Meta 是 pydantic 模型，extra=allow）
- 无 _meta / 无 user_id 字段 → PermissionError（桥层未注入 = 拒绝执行）
- get_profile：user_id 来自 _meta（非模型参数），查询对应用户
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools.mcp_server.main import _user_id, get_profile


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


class FakeUser:
    def __init__(self, user_id: int, nickname: str):
        self.id = user_id
        self.nickname = nickname
        self.community = "兰园"
        self.building = "3栋"
        self.bio = ""
        self.show_building = True
        self.show_room = False


class FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


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
        """user_id 来自 _meta（桥层注入），查询对应用户"""
        fake = FakeSession(FakeScalarResult(FakeUser(42, "桥接用户")))
        with patch("tools.mcp_server.main.async_session_factory", return_value=fake):
            result = await get_profile(ctx=_ctx_with_meta(SimpleNamespace(user_id=42)))

        assert result["id"] == 42
        assert result["nickname"] == "桥接用户"

    @pytest.mark.asyncio
    async def test_profile_ignores_model_arguments(self):
        """工具签名无 user_id 参数——即使有人构造带 user_id 的 arguments 也无法注入（get_profile 只收 ctx）"""
        fake = FakeSession(FakeScalarResult(FakeUser(9, "本人")))
        with patch("tools.mcp_server.main.async_session_factory", return_value=fake):
            result = await get_profile(ctx=_ctx_with_meta(SimpleNamespace(user_id=9)))
        assert result["id"] == 9

    @pytest.mark.asyncio
    async def test_profile_rejects_without_meta(self):
        with pytest.raises(PermissionError):
            await get_profile(ctx=_ctx_with_meta(None))

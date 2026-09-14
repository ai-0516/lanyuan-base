"""pytest 全局配置 — 统一测试数据库"""
import os

import pytest

# 所有测试文件共享同一个 SQLite 文件，避免全局引擎冲突
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_lanyuan.db"
os.environ["WECHAT_CLOUD_DEPLOYMENT"] = "False"


@pytest.fixture(autouse=True)
def mock_wechat_login(monkeypatch):
    """API 测试不访问微信；不同 code 仍映射为不同测试用户。"""
    from app.services import auth_service

    async def code2session(code: str) -> dict:
        return {
            "openid": f"mock_openid_{code}",
            "session_key": "mock_session_key",
            "unionid": None,
        }

    monkeypatch.setattr(auth_service.wechat_client, "code2session", code2session)

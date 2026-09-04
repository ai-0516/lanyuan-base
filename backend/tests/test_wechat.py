"""微信 API 客户端测试（2026-09-04 安全语义修正）

修正背景：code2session 原实现「短 code（<16字符）→ 始终 mock」——生产配置
（真实 WECHAT_APPID）下，外部请求可提交任意短 code 拿到可预测的 mock openid
伪造身份。修正后：mock 只存在于 mock 配置（占位 appid），真实配置一律走真实 API。
"""

import pytest


@pytest.mark.asyncio
async def test_code2session_mock_config_mocks_any_code(monkeypatch):
    """mock 配置（占位 appid）→ 任何 code 都走 mock（开发环境行为）"""
    from app.core import wechat as wechat_module

    monkeypatch.setattr(wechat_module.settings, "WECHAT_APPID", "wx_dev_appid")
    client = wechat_module.WeChatClient()
    assert client._is_mock is True

    result = await client.code2session("short_code_abc")
    assert result["openid"].startswith("mock_openid_")
    # 真实配置分支不应触碰：确认不会走到 httpx（mock 返回已足够）
    assert result["session_key"] == "mock_session_key"


@pytest.mark.asyncio
async def test_code2session_real_appid_never_mocks(monkeypatch):
    """真实 appid 配置（生产）→ 短 code / mock_code 也走真实 API，绝不返回 mock openid"""
    from app.core import wechat as wechat_module

    monkeypatch.setattr(wechat_module.settings, "WECHAT_APPID", "wx_real_appid_123")
    monkeypatch.setattr(wechat_module.settings, "WECHAT_SECRET", "real_secret_value")

    captured = {}

    class FakeResponse:
        def json(self):
            return {"errcode": 40029, "errmsg": "invalid code"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None):
            captured["url"] = url
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr(wechat_module.httpx, "AsyncClient", FakeAsyncClient)

    client = wechat_module.WeChatClient()
    assert client._is_mock is False

    # 短 code（原实现会 mock 的形态）
    with pytest.raises(RuntimeError, match="微信登录失败"):
        await client.code2session("short")
    assert captured["url"] == wechat_module.WECHAT_CODE2SESSION_URL
    assert captured["params"]["js_code"] == "short"
    # 真实 appid/secret 透传
    assert captured["params"]["appid"] == "wx_real_appid_123"

    # mock_code 特殊值同样不得 mock（防可预测 openid）
    captured.clear()
    with pytest.raises(RuntimeError, match="微信登录失败"):
        await client.code2session("mock_code")
    assert captured["params"]["js_code"] == "mock_code"

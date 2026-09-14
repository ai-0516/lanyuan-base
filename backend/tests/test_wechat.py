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


@pytest.mark.asyncio
async def test_msg_sec_check_sends_required_v2_payload(monkeypatch):
    """内容安全调用包含 content/version/scene/openid，并解析微信 suggestion。"""
    from app.core import wechat as wechat_module

    monkeypatch.setattr(wechat_module.settings, "WECHAT_APPID", "wx_real_appid_123")
    monkeypatch.setattr(wechat_module.settings, "WECHAT_SECRET", "real_secret_value")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"errcode": 0, "errmsg": "ok", "result": {"suggest": "risky", "label": 100}}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, params=None, json=None):
            captured.update(url=url, params=params, json=json)
            return FakeResponse()

    monkeypatch.setattr(wechat_module.httpx, "AsyncClient", FakeAsyncClient)
    client = wechat_module.WeChatClient()
    client._access_token = "cached-token"
    client._access_token_expires_at = float("inf")

    suggestion = await client.msg_sec_check(
        "测试内容", "test-openid", scene=wechat_module.WeChatSecurityScene.COMMENT
    )

    assert suggestion == "risky"
    assert captured["url"] == wechat_module.WECHAT_MSG_SEC_CHECK_URL
    assert captured["params"] == {"access_token": "cached-token"}
    assert captured["json"] == {
        "content": "测试内容",
        "version": 2,
        "scene": 2,
        "openid": "test-openid",
    }


@pytest.mark.asyncio
async def test_media_check_async_sends_required_v2_payload(monkeypatch):
    """图片检测包含官方要求的全部 v2 参数，并返回 trace_id。"""
    from app.core import wechat as wechat_module

    monkeypatch.setattr(wechat_module.settings, "WECHAT_APPID", "wx_real_appid_123")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"errcode": 0, "errmsg": "ok", "trace_id": "trace-123"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, params=None, json=None):
            captured.update(url=url, params=params, json=json)
            return FakeResponse()

    monkeypatch.setattr(wechat_module.httpx, "AsyncClient", FakeAsyncClient)
    client = wechat_module.WeChatClient()
    client._access_token = "cached-token"
    client._access_token_expires_at = float("inf")

    trace_id = await client.media_check_async(
        "https://test.tcb.qcloud.la/posts/a.jpg",
        "test-openid",
        scene=wechat_module.WeChatSecurityScene.FORUM,
    )
    assert trace_id == "trace-123"
    assert captured == {
        "url": wechat_module.WECHAT_MEDIA_CHECK_ASYNC_URL,
        "params": {"access_token": "cached-token"},
        "json": {
            "media_url": "https://test.tcb.qcloud.la/posts/a.jpg",
            "media_type": 2,
            "version": 2,
            "scene": 3,
            "openid": "test-openid",
        },
    }

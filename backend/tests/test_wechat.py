"""微信 API 客户端测试。"""

import pytest


@pytest.mark.asyncio
async def test_code2session_requires_local_credentials(monkeypatch):
    """本地真实登录缺少 AppID/Secret 时给出明确错误。"""
    from app.core import wechat as wechat_module

    monkeypatch.setattr(wechat_module.settings, "WECHAT_APPID", "")
    monkeypatch.setattr(wechat_module.settings, "WECHAT_SECRET", "")
    client = wechat_module.WeChatClient()
    with pytest.raises(RuntimeError, match="缺少 WECHAT_APPID 或 WECHAT_SECRET"):
        await client.code2session("short_code_abc")


@pytest.mark.asyncio
async def test_code2session_uses_real_wechat_api(monkeypatch):
    """本地开发使用 AppID/Secret 和临时 code 调微信 API。"""
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
    assert client._is_cloud is False

    with pytest.raises(RuntimeError, match="微信登录失败"):
        await client.code2session("short")
    assert captured["url"] == wechat_module.WECHAT_CODE2SESSION_URL
    assert captured["params"]["js_code"] == "short"
    # 真实 appid/secret 透传
    assert captured["params"]["appid"] == "wx_real_appid_123"

@pytest.mark.asyncio
async def test_msg_sec_check_sends_required_v2_payload(monkeypatch):
    """内容安全调用包含 content/version/scene/openid，并解析微信 suggestion。"""
    from app.core import wechat as wechat_module

    monkeypatch.setattr(wechat_module.settings, "WECHAT_CLOUD_DEPLOYMENT", True)
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

    suggestion = await client.msg_sec_check(
        "测试内容", "test-openid", scene=wechat_module.WeChatSecurityScene.COMMENT
    )

    assert suggestion == "risky"
    assert captured["url"] == wechat_module.WECHAT_CLOUD_MSG_SEC_CHECK_URL
    assert captured["params"] is None
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

    monkeypatch.setattr(wechat_module.settings, "WECHAT_CLOUD_DEPLOYMENT", True)
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

    trace_id = await client.media_check_async(
        "https://test.tcb.qcloud.la/posts/a.jpg",
        "test-openid",
        scene=wechat_module.WeChatSecurityScene.FORUM,
    )
    assert trace_id == "trace-123"
    assert captured == {
        "url": wechat_module.WECHAT_CLOUD_MEDIA_CHECK_ASYNC_URL,
        "params": None,
        "json": {
            "media_url": "https://test.tcb.qcloud.la/posts/a.jpg",
            "media_type": 2,
            "version": 2,
            "scene": 3,
            "openid": "test-openid",
        },
    }

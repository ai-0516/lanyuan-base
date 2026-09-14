"""微信 API 客户端

部署模式：
- 本地开发通过 AppID/Secret 调用 code2session，内容安全使用 mock
- 微信云托管从可信 header 获取用户身份，内容安全调用内部 API
"""

from enum import IntEnum

import httpx

from app.config import settings

WECHAT_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"
WECHAT_CLOUD_MSG_SEC_CHECK_URL = "http://api.weixin.qq.com/wxa/msg_sec_check"
WECHAT_CLOUD_MEDIA_CHECK_ASYNC_URL = "http://api.weixin.qq.com/wxa/media_check_async"


class WeChatSecurityScene(IntEnum):
    """微信内容安全 API 的发布场景。"""

    COMMENT = 2
    FORUM = 3


class WeChatClient:
    """微信客户端"""

    def __init__(self):
        self._is_cloud = settings.WECHAT_CLOUD_DEPLOYMENT

    async def code2session(self, code: str) -> dict:
        """用临时 code 换取 openid / session_key

        仅本地开发链路使用；云托管登录直接采用平台注入的 x-wx-openid。
        """
        if not settings.WECHAT_APPID or not settings.WECHAT_SECRET:
            raise RuntimeError("本地微信登录缺少 WECHAT_APPID 或 WECHAT_SECRET")

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                WECHAT_CODE2SESSION_URL,
                params={
                    "appid": settings.WECHAT_APPID,
                    "secret": settings.WECHAT_SECRET,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
            result = resp.json()

        if "openid" not in result:
            raise RuntimeError(
                f"微信登录失败: {result.get('errmsg', '未知错误')}"
            )

        return result

    async def msg_sec_check(
        self, content: str, openid: str, scene: WeChatSecurityScene
    ) -> str:
        """检查公开文本，返回微信建议：pass / review / risky。"""
        if not self._is_cloud:
            return "pass"

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                WECHAT_CLOUD_MSG_SEC_CHECK_URL,
                json={
                    "content": content,
                    "version": 2,
                    "scene": int(scene),
                    "openid": openid,
                },
            )
            resp.raise_for_status()
            result = resp.json()

        if result.get("errcode", 0) != 0:
            raise RuntimeError(f"微信内容安全校验失败: {result.get('errmsg', '未知错误')}")
        suggestion = result.get("result", {}).get("suggest")
        if suggestion not in {"pass", "review", "risky"}:
            raise RuntimeError("微信内容安全校验返回无效结果")
        return suggestion

    async def media_check_async(
        self, media_url: str, openid: str, scene: WeChatSecurityScene
    ) -> str | None:
        """提交图片异步检测，返回用于匹配微信回调的 trace_id。"""
        if not self._is_cloud:
            return None

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                WECHAT_CLOUD_MEDIA_CHECK_ASYNC_URL,
                json={
                    "media_url": media_url,
                    "media_type": 2,
                    "version": 2,
                    "scene": int(scene),
                    "openid": openid,
                },
            )
            resp.raise_for_status()
            result = resp.json()

        if result.get("errcode", 0) != 0 or not result.get("trace_id"):
            raise RuntimeError(f"微信图片安全校验提交失败: {result.get('errmsg', '未知错误')}")
        return result["trace_id"]


wechat_client = WeChatClient()

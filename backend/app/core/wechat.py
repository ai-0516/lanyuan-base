"""微信 API 客户端

自动判断模式：
- WECHAT_APPID 为默认占位值时 → mock 模式（开发环境）
- 配置了真实 appid/secret 时 → 调微信 API
"""

from enum import IntEnum

import httpx

from app.config import settings

WECHAT_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"
WECHAT_CLOUD_MSG_SEC_CHECK_URL = "http://api.weixin.qq.com/wxa/msg_sec_check"
WECHAT_CLOUD_MEDIA_CHECK_ASYNC_URL = "http://api.weixin.qq.com/wxa/media_check_async"


class WeChatSecurityScene(IntEnum):
    """微信内容安全 API 的发布场景。"""

    PROFILE = 1
    COMMENT = 2
    FORUM = 3
    SOCIAL_LOG = 4


class WeChatClient:
    """微信客户端"""

    def __init__(self):
        self._is_mock = settings.WECHAT_APPID in ("wx_dev_appid", "")

    async def code2session(self, code: str) -> dict:
        """用临时 code 换取 openid / session_key

        策略（双模式共存）：
        - mock 配置（WECHAT_APPID 为占位值）→ 全走 mock（开发环境）
        - 真实 appid 配置（生产）→ 一律调真实微信 API（含短 code / mock_code，
          2026-09-04 修正：短 code 启发式会让生产环境可伪造 mock openid
          绕过身份——mock 只允许存在于 mock 配置）
        """
        if self._is_mock:
            return self._mock_code2session(code)

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

    @staticmethod
    def _mock_code2session(code: str) -> dict:
        """模拟 code 换 session_key + openid"""
        if code == "mock_code":
            openid = "test_openid_0"
        else:
            openid = f"mock_openid_{hash(code) % 100000:05d}"

        return {
            "openid": openid,
            "session_key": "mock_session_key",
            "unionid": None,
        }

    async def msg_sec_check(
        self, content: str, openid: str, scene: WeChatSecurityScene
    ) -> str:
        """检查公开文本，返回微信建议：pass / review / risky。"""
        if self._is_mock:
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
        if self._is_mock:
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

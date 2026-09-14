"""微信 API 客户端

自动判断模式：
- WECHAT_APPID 为默认占位值时 → mock 模式（开发环境）
- 配置了真实 appid/secret 时 → 调微信 API
"""

import asyncio
import time

import httpx

from app.config import settings

WECHAT_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"
WECHAT_ACCESS_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
WECHAT_MSG_SEC_CHECK_URL = "https://api.weixin.qq.com/wxa/msg_sec_check"
WECHAT_MEDIA_CHECK_ASYNC_URL = "https://api.weixin.qq.com/wxa/media_check_async"


class WeChatClient:
    """微信客户端"""

    def __init__(self):
        self._is_mock = settings.WECHAT_APPID in ("wx_dev_appid", "")
        self._access_token = ""
        self._access_token_expires_at = 0.0
        self._access_token_lock = asyncio.Lock()

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

    async def get_access_token(self) -> str:
        """获取并缓存微信接口调用凭据（开发环境返回 mock）。"""
        if self._is_mock:
            return "mock_access_token"

        if self._access_token and time.monotonic() < self._access_token_expires_at:
            return self._access_token

        async with self._access_token_lock:
            if self._access_token and time.monotonic() < self._access_token_expires_at:
                return self._access_token
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    WECHAT_ACCESS_TOKEN_URL,
                    params={
                        "grant_type": "client_credential",
                        "appid": settings.WECHAT_APPID,
                        "secret": settings.WECHAT_SECRET,
                    },
                )
                resp.raise_for_status()
                result = resp.json()

            token = result.get("access_token", "")
            if not token:
                raise RuntimeError(f"获取微信 access token 失败: {result.get('errmsg', '未知错误')}")
            expires_in = max(int(result.get("expires_in", 7200)) - 300, 60)
            self._access_token = token
            self._access_token_expires_at = time.monotonic() + expires_in
            return token

    async def msg_sec_check(self, content: str, openid: str, scene: int) -> str:
        """检查公开文本，返回微信建议：pass / review / risky。"""
        if self._is_mock:
            return "pass"

        token = await self.get_access_token()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                WECHAT_MSG_SEC_CHECK_URL,
                params={"access_token": token},
                json={
                    "content": content,
                    "version": 2,
                    "scene": scene,
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
        self, media_url: str, openid: str, scene: int
    ) -> str | None:
        """提交图片异步检测，返回用于匹配微信回调的 trace_id。"""
        if self._is_mock:
            return None

        token = await self.get_access_token()
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                WECHAT_MEDIA_CHECK_ASYNC_URL,
                params={"access_token": token},
                json={
                    "media_url": media_url,
                    "media_type": 2,
                    "version": 2,
                    "scene": scene,
                    "openid": openid,
                },
            )
            resp.raise_for_status()
            result = resp.json()

        if result.get("errcode", 0) != 0 or not result.get("trace_id"):
            raise RuntimeError(f"微信图片安全校验提交失败: {result.get('errmsg', '未知错误')}")
        return result["trace_id"]


wechat_client = WeChatClient()

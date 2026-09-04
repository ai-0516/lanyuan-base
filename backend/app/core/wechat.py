"""微信 API 客户端

自动判断模式：
- WECHAT_APPID 为默认占位值时 → mock 模式（开发环境）
- 配置了真实 appid/secret 时 → 调微信 API
"""

import httpx

from app.config import settings

WECHAT_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


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

    async def get_access_token(self) -> str:
        """获取微信接口调用凭据（开发环境返回 mock）"""
        if self._is_mock:
            return "mock_access_token"

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.weixin.qq.com/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": settings.WECHAT_APPID,
                    "secret": settings.WECHAT_SECRET,
                },
            )
            result = resp.json()

        return result.get("access_token", "")


wechat_client = WeChatClient()

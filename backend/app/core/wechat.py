"""微信 API 客户端

开发环境模拟微信登录，生产环境需替换为真实微信 API 调用。
"""

from app.config import settings


class WeChatClient:
    """模拟微信客户端"""

    async def code2session(self, code: str) -> dict:
        """模拟 code 换 session_key + openid"""
        # 生产环境：调 https://api.weixin.qq.com/sns/jscode2session
        # 开发环境：根据 code 生成模拟 openid
        if code == "mock_code":
            return {
                "openid": "test_openid_0",
                "session_key": "mock_session_key",
                "unionid": None,
            }
        # 用 code 的 hash 生成稳定的 mock openid，方便测试
        openid = f"mock_openid_{hash(code) % 100000:05d}"
        return {
            "openid": openid,
            "session_key": "mock_session_key",
            "unionid": None,
        }

    async def get_access_token(self) -> str:
        """获取微信接口调用凭据"""
        return "mock_access_token"


wechat_client = WeChatClient()

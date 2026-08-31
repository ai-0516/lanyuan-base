"""认证与密钥工具：JWT Token + MCP 内部共享密钥"""

import hmac
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ── MCP 内部认证（v2 M2，PR #94 review 修复）──
# /mcp 以 streamable-http 挂载在公网 FastAPI，任何网络可达者可直连并伪造
# callTool 的 _meta.user_id 冒充任意用户（devlead review 实测越权）——桥层注入
# 只是约定不是强制，必须把「谁可以调用 /mcp」钉死在传输层。
# 方案：内部共享密钥 header。桥插件（DSH 子进程）所有请求带 X-Lanyuan-Internal-Token，
# server 校验通过才处理（tools/list 也在门内）；失败 401。_meta.user_id 的信任前提
# 从「任意网络可达者」收紧为「持有内部 token 的 client」——唯一持有者是本进程
# DSH 子进程（user_id 由其从 session id 解析注入），外部 client 无法到达业务工具。
# 为什么不走 JWT/用户认证：/mcp 的唯一合法 client 是本进程 DSH 子进程（内部通道），
# 不是最终用户——用户身份由桥从 session id 绑定（§6.3），传输层只需要防外部直连。
MCP_AUTH_HEADER = "x-lanyuan-internal-token"
# 显式配置（生产）：LANYUAN_MCP_TOKEN 同时注入 FastAPI 与 DSH 子进程 env
# （dsh_runtime._runtime_env 注入同值）；未配置（开发）→ 进程内自动生成随机
# 密钥，外部无法猜测，零配置开发（默认安全，fail-closed）
_mcp_token: str | None = None
_mcp_token_lock = threading.Lock()


def get_mcp_token() -> str:
    """MCP 内部共享密钥：显式 env 优先，未配置则进程内生成一次（跨线程安全）。

    同一进程内唯一值——FastAPI 中间件与 DSH 子进程 env 注入都读这里，
    谁先触发生成，另一方读到同值。
    """
    global _mcp_token
    with _mcp_token_lock:
        if _mcp_token is None:
            _mcp_token = os.environ.get("LANYUAN_MCP_TOKEN") or secrets.token_urlsafe(32)
        return _mcp_token


def verify_mcp_token(value: str | None) -> bool:
    """校验内部密钥（常量时间比较，防时序侧信道）"""
    if not value:
        return False
    return hmac.compare_digest(value, get_mcp_token())


def create_access_token(user_id: int) -> str:
    """创建 JWT Token"""
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRES_HOURS)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """解码 JWT Token，失败返回 None"""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except JWTError:
        return None

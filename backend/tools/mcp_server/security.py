"""MCP 端点内部认证中间件（v2 M2，PR #94 review 修复）

背景：MCP server 以 streamable-http 挂在 FastAPI /mcp（app.main mount），review
实测**无认证直连 + 伪造 _meta.user_id 可冒充任意用户**调用全部 19 个业务工具
（改他人资料/冒充发帖）。`_meta` 是 MCP 协议中 client 自由填写的字段，桥层注入
只是约定不是强制——必须把「谁可以调用 /mcp」钉死在传输层。

方案：内部共享密钥 header（review 建议方向一）。桥插件（DSH 子进程）所有请求
（含 GET 初始化）带 `X-Lanyuan-Internal-Token`，本中间件在 streamable-http 路由
之前校验，失败直接 401（tools/list 也在门内）；`_meta.user_id` 的信任前提从
「任意网络可达者」收紧为「持有内部 token 的 client」——唯一持有者是本进程 DSH
子进程（user_id 由其从 session id 解析注入，§6.3），外部 client 无法到达业务工具。

密钥来源见 app.core.security.get_mcp_token（显式 env LANYUAN_MCP_TOKEN 优先，
未配置进程内自动生成——dsh_runtime 注入同值到 DSH 子进程 env，零配置开发）。
"""

from __future__ import annotations

from starlette.responses import JSONResponse

from app.core.security import MCP_AUTH_HEADER, verify_mcp_token


class McpAuthMiddleware:
    """ASGI 中间件：校验内部共享密钥 header，未认证请求 401。

    挂在 `mcp.http_app(middleware=[Middleware(McpAuthMiddleware)])`——包裹整个
    MCP 子 app，GET（SSE 初始化）/POST（JSON-RPC）/DELETE 全部请求都在门内。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        if not verify_mcp_token(headers.get(MCP_AUTH_HEADER)):
            response = JSONResponse(
                status_code=401,
                content={"code": 401, "message": "未认证：MCP 内部端点，拒绝外部 client 访问"},
            )
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)

"""lanyuan 业务工具 MCP server（TECH_SPEC §6）：fastmcp，streamable-http transport

**挂载在 FastAPI（app.main /mcp 端点，M2 review 定）**——MCP server 能力独立于
DSH runtime（DSH 只是 HTTP client），工具/API 同进程、同认证/事务体系。

工具注册（§6.4b）：**@mcp_tool 原生注册，不依赖 v1 @tool**——工具定义在 v2
endpoint 上（app/api/v2/profile.py，@router + @mcp_tool 叠加，用法同 v1 @tool），
app.main import v2 模块时装饰器执行即注册（工具即注册，无注册表遍历/无 v1
依赖）。v1 工具体系退役后 MCP 侧零影响；v1 的 search_history 不迁移（v2 历史
搜索用 DSH session-query）。

身份设计（§6.3）：工具签名不含 user_id/db（LLM 不可见）；`user_id` 由 @mcp_tool
从 callTool 请求 `_meta` 注入（桥层强制绑定，LLM 无法伪造；`_meta` 是 MCP 协议
字段，与传输无关，HTTP 同样透传）；`db` 由 @mcp_tool 注入会话（请求级 commit）。
任何工具实现不得信任模型输入中的身份字段。

挂载：app.main `app.mount("/mcp", mcp_app)`；DSH 桥 StreamableHTTPClientTransport
连接 LANYUAN_MCP_URL（§6.2）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # backend/

from tools.mcp_server.decorator import mcp  # noqa: E402

# streamable-http ASGI app（§6.2：挂载到 FastAPI /mcp 端点）。
# path="/"：fastmcp 默认 streamable_http_path=/mcp，与 FastAPI mount 前缀叠加
# 会 404（实测）——挂载后请求 /mcp/ → strip 前缀 → 子 app 根路径命中
mcp_app = mcp.http_app(path="/", transport="streamable-http")

"""FastAPI 应用入口"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.core.database import init_db, close_db
from app.logger import setup_logging
from app.ai.dsh_runtime import dsh_runtime
from app.api.v1 import auth, posts, comments, notifications, profile, ai, upload, memory
from app.api.v2 import ai as v2_ai
# v2 工具=endpoint 模块（§6.4b：@mcp_tool 写在 endpoint 上）——import 即触发
# @mcp_tool 注册进 mcp（必须在 tools.mcp_server.main 的 mcp_app 构建前 import）
from app.api.v2 import profile as v2_profile
from app.api.response import api_exception_handler, api_success, validation_exception_handler
# v2 MCP server（§6.2：挂载 /mcp，streamable-http）——lifespan 需合并（fastmcp
# StreamableHTTPSessionManager 依赖 lifespan 初始化，官方要求显式传入父 app）
from tools.mcp_server.main import mcp_app  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()
    # fastmcp session manager 初始化（挂载子 app 的 lifespan 需显式合并）
    async with mcp_app.lifespan(app):
        # v2 DSH runtime 预热（§3.1）。HTTP 模式：MCP server 挂 FastAPI，桥连接
        # 需要 FastAPI 已 listen（lifespan 完成后）——预热改后台任务避免启动
        # 死锁（桥重试窗口内 listen 后自动连上）；首请求前未完成则懒启动兜底
        asyncio.create_task(asyncio.to_thread(dsh_runtime.start))
        yield
        dsh_runtime.close()
    await close_db()


app = FastAPI(
    title=settings.APP_NAME,
    version="3.0.0",
    lifespan=lifespan,
)

# ── 统一异常处理器 ──
# Starlette 对 HTTPException 是精确键查找，不会回退到 Exception 处理器，
# 必须显式注册，否则 401/404/422 走默认格式（顶层无 code）（#27）
app.add_exception_handler(Exception, api_exception_handler)
app.add_exception_handler(StarletteHTTPException, api_exception_handler)
# RequestValidationError 是独立注册的 key（FastAPI 默认），MRO 精确命中不走
# HTTPException 处理器；且 0.141+ 已非 HTTPException 子类，需专用 handler（#27）
app.add_exception_handler(RequestValidationError, validation_exception_handler)

# ── API 路由 ──
app.include_router(auth.router, prefix="/api/v1")
app.include_router(posts.router, prefix="/api/v1")
app.include_router(comments.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(profile.router, prefix="/api/v1")
app.include_router(ai.router, prefix="/api/v1")
app.include_router(memory.router, prefix="/api/v1")
app.include_router(upload.router, prefix="/api/v1")

# v2（DSH 重写 agent，TECH_SPEC §9.1）
app.include_router(v2_ai.router, prefix="/api/v2")
# v2 工具=endpoint（§6.4b：get_my_profile 双形态——HTTP /api/v2/user/me + MCP 工具）
app.include_router(v2_profile.router, prefix="/api/v2")

# ── v2 MCP server 挂载（§6.2：业务工具 MCP server，streamable-http） ──
# MCP server 能力独立于 DSH runtime（DSH 桥经 HTTP 消费）；工具/API 同进程
app.mount("/mcp", mcp_app, name="mcp")


@app.get("/api/health")
async def health():
    return api_success({"status": "ok", "app": settings.APP_NAME})


# ── 静态文件 ──
uploads_dir = Path(settings.UPLOAD_DIR)
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

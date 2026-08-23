"""FastAPI 应用入口"""

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
from app.api.response import api_exception_handler, api_success, validation_exception_handler


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()
    # v2 DSH runtime 预热（TECH_SPEC §3.1：worker 启动即常驻，首次请求无 spawn 延迟）
    dsh_runtime.harness  # noqa: B018 触发启动（DeepSeekHarness.__enter__）
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


@app.get("/api/health")
async def health():
    return api_success({"status": "ok", "app": settings.APP_NAME})


# ── 静态文件 ──
uploads_dir = Path(settings.UPLOAD_DIR)
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

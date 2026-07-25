"""FastAPI 应用入口"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.database import init_db, close_db
from app.logger import setup_logging
from app.api.v1 import auth, posts, comments, notifications, profile, ai, upload
from app.api.response import api_exception_handler, api_success


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title=settings.APP_NAME,
    version="3.0.0",
    lifespan=lifespan,
)

# ── 统一异常处理器 ──
app.add_exception_handler(Exception, api_exception_handler)

# ── API 路由 ──
app.include_router(auth.router, prefix="/api/v1")
app.include_router(posts.router, prefix="/api/v1")
app.include_router(comments.router, prefix="/api/v1")
app.include_router(notifications.router, prefix="/api/v1")
app.include_router(profile.router, prefix="/api/v1")
app.include_router(ai.router, prefix="/api/v1")
app.include_router(upload.router, prefix="/api/v1")


@app.get("/api/health")
async def health():
    return api_success({"status": "ok", "app": settings.APP_NAME})


# ── 静态文件 ──
uploads_dir = Path(settings.UPLOAD_DIR)
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

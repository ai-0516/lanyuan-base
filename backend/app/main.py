"""FastAPI 应用入口"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.database import init_db, close_db
from app.api.v1 import auth, posts, comments, notifications, profile, ai, upload


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title=settings.APP_NAME,
    version="3.0.0",
    lifespan=lifespan,
)

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
    return {"status": "ok", "app": settings.APP_NAME}


# ── 静态文件 ──
uploads_dir = Path(settings.UPLOAD_DIR)
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

# 前端（本地开发）
frontend_dir = (Path(__file__).parent.parent.parent / "frontend").resolve()
if frontend_dir.exists():
    @app.get("/")
    async def serve_index():
        return FileResponse(str(frontend_dir / "index.html"))

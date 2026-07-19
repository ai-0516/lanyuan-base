"""图片上传 API"""

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.config import settings

router = APIRouter(prefix="/upload", tags=["上传"])

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


@router.post("/images")
async def upload_images(
    files: list[UploadFile],
    user_id: int = Depends(get_current_user),
):
    """上传图片（1-9张），返回 URL 列表"""
    if len(files) < 1 or len(files) > 9:
        raise HTTPException(status_code=400, detail="上传图片数量需在 1-9 张之间")

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

    urls = []
    for file in files:
        ext = os.path.splitext(file.filename or ".jpg")[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式: {ext}",
            )

        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"文件过大（最大 10MB）: {file.filename}",
            )

        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(settings.UPLOAD_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(content)

        urls.append(f"/uploads/{filename}")

    return {"urls": urls}

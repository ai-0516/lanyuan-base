"""统一 API 响应格式

所有非 SSE 接口统一使用:

成功: {"code": 0, "data": ..., "message": "ok"}
失败: {"code": 40001, "message": "错误描述"}
"""

import logging

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def api_success(data=None, message: str = "ok") -> dict:
    """成功响应"""
    return {"code": 0, "data": data, "message": message}


def api_error(code: int, message: str, status_code: int = 400):
    """抛出标准错误，由异常处理器格式化"""
    raise HTTPException(status_code=status_code, detail={"code": code, "message": message})


def unauthorized(message: str = "未登录或 Token 已过期"):
    """401 未授权"""
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": 40001, "message": message},
    )


async def api_exception_handler(request, exc):
    """FastAPI 异常处理器，将异常转为统一格式"""
    if hasattr(exc, "detail"):
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            return JSONResponse(
                status_code=exc.status_code,
                content=detail,
            )
        # 兼容旧的 detail 字符串格式
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code * 100, "message": str(detail)},
        )
    # 非 HTTPException（如 Pydantic ValidationError、SQL 错误等）
    logger.exception("未捕获的异常: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": 50000, "message": "服务器内部错误"},
    )

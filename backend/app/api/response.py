"""统一 API 响应格式

所有非 SSE 接口统一使用:

成功: {"code": 0, "data": ..., "message": "ok"}
失败: {"code": 40001, "message": "错误描述"}
"""

import logging

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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


async def validation_exception_handler(request, exc):
    """422 请求参数校验失败 → 统一格式。

    FastAPI 0.141+ 的 RequestValidationError 不再是 HTTPException 子类
    （无 detail/status_code），不能复用 api_exception_handler（#27）。
    """
    errors = getattr(exc, "errors", lambda: [])()
    message = errors[0].get("msg", "请求参数错误") if errors else "请求参数错误"
    return JSONResponse(
        status_code=422,
        content={"code": 42200, "message": message},
    )


async def api_exception_handler(request, exc):
    """FastAPI 异常处理器，将异常转为统一格式"""
    status_code = getattr(exc, "status_code", 500)
    # 入口统一记录：所有进 handler 的异常都可追踪（含 detail 分支——该分支
    # 曾无日志，导致 #64 事故的 500 在 error.log/app.log 无迹可查）
    logger.warning("API 异常: %s %s → %s (%s)", request.method, request.url.path, status_code, type(exc).__name__)
    # 仅 HTTPException 有业务 detail（dict 透传 / 字符串兼容 / 404 默认文案）。
    # 用 StarletteHTTPException 判断：fastapi.HTTPException 是其子类（独立类，
    # 非 re-export），路由 404/401 抛的则是 Starlette 本身——两者都要覆盖（#64）。
    # 不能按 hasattr(exc, "detail") 判断——SQLAlchemy 等 DB 异常也有 detail
    # 属性（值为 []），会被误判进此分支导致不打日志、文案错误（#64）
    if isinstance(exc, StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            return JSONResponse(
                status_code=status_code,
                content=detail,
            )
        # 兼容旧的 detail 字符串格式；无 detail（如 404）给默认文案
        message = str(detail) if detail else "Not Found"
        return JSONResponse(
            status_code=status_code,
            content={"code": status_code * 100, "message": message},
        )
    # 非 HTTPException（如 Pydantic ValidationError、SQL 错误等）→ 不暴露内部细节
    logger.exception("未捕获的异常: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": 50000, "message": "服务器内部错误"},
    )

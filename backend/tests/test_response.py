"""异常处理器测试：HTTPException 与未捕获异常（如 SQL 错误）的统一格式

#64 事故回归测试：
- 修复前 SQLAlchemy 异常带 detail=[] 属性，被 hasattr(exc, "detail") 误判进
  detail 分支 → 返回 "Not Found" 文案且不记录日志
- 修复后仅 isinstance(exc, HTTPException) 走 detail 分支，
  SQL 等未捕获异常 → logger.exception + "服务器内部错误"
"""

import logging

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.response import api_exception_handler, validation_exception_handler


def _make_app(exc: Exception) -> FastAPI:
    """构造注册统一异常处理器的临时 app，/boom 抛指定异常"""
    app = FastAPI()
    app.add_exception_handler(Exception, api_exception_handler)
    app.add_exception_handler(StarletteHTTPException, api_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    @app.get("/boom")
    async def boom():
        raise exc

    return app


def test_sql_exception_logs_and_returns_500(caplog):
    """SQLAlchemy 数据库异常（#64 事故场景）：
    记录 error.log 日志 + 返回统一 500，不暴露内部细节
    """
    exc = OperationalError("SELECT count(posts.id) FROM posts", {}, Exception("conn lost"))
    app = _make_app(exc)
    with caplog.at_level(logging.ERROR):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/boom")

    assert resp.status_code == 500
    assert resp.json() == {"code": 50000, "message": "服务器内部错误"}
    assert any("未捕获的异常" in r.getMessage() for r in caplog.records)


def test_business_error_detail_dict_passthrough():
    """业务错误（HTTPException detail dict）原样透传"""
    app = _make_app(HTTPException(status_code=400, detail={"code": 40001, "message": "参数错误"}))
    with TestClient(app) as client:
        resp = client.get("/boom")

    assert resp.status_code == 400
    assert resp.json() == {"code": 40001, "message": "参数错误"}


def test_http_exception_string_detail_uses_status_code():
    """HTTPException 字符串 detail → code=status*100 + 文案"""
    app = _make_app(HTTPException(status_code=401, detail="Not authenticated"))
    with TestClient(app) as client:
        resp = client.get("/boom")

    assert resp.status_code == 401
    assert resp.json() == {"code": 40100, "message": "Not authenticated"}


def test_http_exception_no_detail_default_not_found():
    """HTTPException 无 detail（如 404）→ 默认 "Not Found" 文案"""
    app = _make_app(HTTPException(status_code=404))
    with TestClient(app) as client:
        resp = client.get("/boom")

    assert resp.status_code == 404
    assert resp.json() == {"code": 40400, "message": "Not Found"}

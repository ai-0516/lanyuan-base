"""
大结果监控 — 检测 tool 返回尺寸异常并记到独立文件

当 tool result 超过阈值时写入 logs/tool-oversize.log，
用来判断哪些 tool 需要加 result_formatter。
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler

from app.harness.hooks import events
from app.harness.hooks.events import on

# ── 专用日志器：写到独立文件 ──

_logger = logging.getLogger("app.harness.hooks.oversize")
_logger.propagate = False  # 不往上冒，避免重复到 app.log

if not _logger.handlers:
    log_dir = os.environ.get("LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = TimedRotatingFileHandler(
        os.path.join(log_dir, "tool-oversize.log"),
        when="midnight", backupCount=30,
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(req_id)s] tool=%(tool_name)s result_len=%(result_len)d"
    ))
    _logger.addHandler(handler)
    _logger.setLevel(logging.WARNING)

# 普通日志器（propagate 到 app.log），每回都记
_oversize_logger = logging.getLogger("app.harness.hooks.large_tool")

# 初始阈值设大，收集数据后再调
_THRESHOLD = 50000  # bytes


@on(events.TOOL_END)
async def check_tool_size(data: dict):
    result = data.get("result", "")
    result_len = len(result)
    req_id = data.get("req_id", "-")
    tool_name = data.get("tool_name", "?")

    # 每回都记到 app.log，作为调阈值的参考
    _oversize_logger.info(
        "[%s] [tool:end] tool=%s result_len=%d threshold=%d",
        req_id, tool_name, result_len, _THRESHOLD,
    )

    if result_len <= _THRESHOLD:
        return

    _logger.warning(
        "tool result oversized",
        extra={
            "req_id": req_id,
            "tool_name": tool_name,
            "result_len": result_len,
        },
    )

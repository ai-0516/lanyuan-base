"""日志模块

提供统一的日志配置，支持多目标输出。

用法：
    # 在应用启动时初始化一次
    from app.logger import setup_logging
    setup_logging()

    # 在各模块中直接用标准 logging
    import logging
    logger = logging.getLogger(__name__)

目标模式（通过 LOG_TARGET 切换）：
    local  — 开发环境：控制台 + 文件（按天轮转）
    oss    — 生产环境：文件 + 自动上传到 OSS（占位，待实现）
"""

import logging
import logging.handlers
from pathlib import Path

from app.config import settings


def setup_logging():
    """根据配置初始化根 logger

    应在应用启动时（lifespan）调用一次。
    """
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # 清除已有 handler（避免重复初始化）
    root.handlers.clear()

    # ── Console Handler（所有环境） ──
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(_formatter())
    root.addHandler(console)

    # ── File Handler（按日轮转） ──
    log_dir = Path(settings.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    # 应用日志
    app_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_dir / "app.log"),
        when="midnight",
        interval=1,
        backupCount=30,          # 保留 30 天
        encoding="utf-8",
    )
    app_handler.setLevel(level)
    app_handler.setFormatter(_formatter(include_location=True))
    root.addHandler(app_handler)

    # ── 错误日志（独立文件，仅 ERROR 以上） ──
    err_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_dir / "error.log"),
        when="midnight",
        interval=1,
        backupCount=90,
        encoding="utf-8",
    )
    err_handler.setLevel(logging.ERROR)
    err_handler.setFormatter(_formatter(include_location=True))
    root.addHandler(err_handler)

    # ── OSS 目标（占位） ──
    # 当 LOG_TARGET=oss 时，日志文件写好后自动上传到 OSS。
    # 待后续接入 OSS SDK 后实现：
    #   1. 继承 TimedRotatingFileHandler
    #   2. 在 doRollover() 中触发 oss.upload()
    if settings.LOG_TARGET == "oss":
        root.warning("LOG_TARGET=oss: OSS upload not yet implemented, falling back to local")

    logging.info(
        "日志系统已初始化: level=%s target=%s dir=%s",
        settings.LOG_LEVEL,
        settings.LOG_TARGET,
        log_dir,
    )


def _formatter(include_location: bool = False) -> logging.Formatter:
    """日志格式

    开发环境简洁（不含调用位置），文件日志含调用位置。
    """
    if include_location:
        fmt = (
            "[%(asctime)s.%(msecs)03d] %(levelname)-5s %(name)s:%(lineno)d"
            " — %(message)s"
        )
    else:
        fmt = "[%(asctime)s.%(msecs)03d] %(levelname)-5s %(name)s — %(message)s"
    return logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

"""跨会话记忆 harness（#9）— 对外唯一入口

外部 service/API/hook 只 import 本包（from app.harness import memory），
memory provider 是内部实现细节，对外完全透明。

内部结构：
- memory.py              模块级接口（add/delete/list_all/search/extract/
                         consolidate/select_relevant/build_memory_*）
- memory_provider.py     MemoryProvider 抽象层 + 常量
- memory_provider_db.py  DBMemoryProvider（数据库版实现，每 provider 独立文件）
"""

from .memory import (
    add,
    build_memory_body,
    build_memory_description,
    consolidate,
    delete,
    extract,
    list_all,
    search,
    select_relevant,
)
from .memory_provider import VALID_TYPES, MemoryLimitError

__all__ = [
    "add",
    "delete",
    "list_all",
    "search",
    "extract",
    "consolidate",
    "select_relevant",
    "build_memory_description",
    "build_memory_body",
    "VALID_TYPES",
    "MemoryLimitError",
]

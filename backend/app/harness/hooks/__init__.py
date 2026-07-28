"""
钩子系统 — 导入即激活

导入事件机制和所有内置钩子文件。
每个钩子文件通过 @on(...) 装饰器在导入时自动注册。
"""

from . import events  # noqa: F401 — 事件系统
from . import jsonl  # noqa: F401 — JSONL 日志
from . import log  # noqa: F401 — 终端日志
from . import stats  # noqa: F401 — Token 统计

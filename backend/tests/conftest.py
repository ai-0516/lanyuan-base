"""pytest 全局配置 — 统一测试数据库"""
import os

# 所有测试文件共享同一个 SQLite 文件，避免全局引擎冲突
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_lanyuan.db"

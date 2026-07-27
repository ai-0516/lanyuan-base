"""工具定义（简化为：完全委托 registry）

TOOLS 为惰性属性，在第一次访问时从 registry 动态生成。
"""

from app.harness.tool_registry import registry


async def execute_tool(db, user_id: int, tool_call: dict) -> str:
    """向后兼容的 execute_tool"""
    return await registry.execute(db, user_id, tool_call)


def __getattr__(name):
    if name == "TOOLS":
        return registry.to_openai_tools()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Spike 3: 模拟 lanyuan 业务工具的 Python MCP server

模拟 2 个真实业务工具（search_history / get_profile），用 fastmcp 实现，
stdio transport。真实 v2 中这里会复用 @tool 装饰器生成的 schema 与执行逻辑。
"""
from fastmcp import FastMCP

mcp = FastMCP("lanyuan")

# 模拟 MySQL 数据
_FAKE_HISTORY = [
    {"message_id": 101, "role": "user", "content": "我想给家里的地暖调低温度"},
    {"message_id": 102, "role": "assistant", "content": "好的，已帮您把地暖从 25°C 调到 22°C"},
    {"message_id": 103, "role": "user", "content": "物业电话是多少？"},
    {"message_id": 104, "role": "assistant", "content": "物业电话是 400-123-4567"},
]

_FAKE_PROFILE = {
    "nickname": "测试用户",
    "community": "兰园小区",
    "building": "3栋",
    "unit": "2单元",
    "room": "1201",
}


@mcp.tool()
def search_history(query: str, limit: int = 3) -> dict:
    """搜索用户过往对话历史。当用户提到过去聊过的内容时使用。"""
    results = [
        m for m in _FAKE_HISTORY if query in m["content"]
    ][:limit]
    return {"results": results, "total": len(results)}


@mcp.tool()
def get_profile() -> dict:
    """获取当前用户的基本资料（昵称、社区、楼栋单元房号）。"""
    return dict(_FAKE_PROFILE)


if __name__ == "__main__":
    mcp.run()  # stdio transport

"""tool formatter 全覆盖验证（issue #68）

- 所有 app.api 模块注册的 @tool 都带 result_formatter（formatter 是唯一主路径，
  不再靠全局 base64 清洗兜底）
- 代表性 formatter 行为：隐私字段收敛、base64 移除、搜索结果结构
"""

from app.main import app  # noqa: F401,E402 全量 import 触发 @tool 注册
from app.harness.tool_registry import registry
from app.api.v1.ai import _format_search_history
from app.api.v1.memory import _format_memory_list
from app.api.v1.posts import _format_post
from app.api.v1.profile import _format_user_profile


def test_all_api_tools_have_formatter():
    """app.api 下注册的 tool 全部带 result_formatter"""
    missing = [
        t.name
        for t in registry.all
        if t.fn.__module__.startswith("app.api.") and t.result_formatter is None
    ]
    assert missing == [], f"以下 tool 缺 result_formatter: {missing}"


def test_profile_formatter_hides_privacy_fields():
    """我的资料 formatter 不暴露 openid/unionid/房号/base64 头像"""
    data = {
        "id": 1, "openid": "wx-openid", "unionid": "u-1",
        "nickname": "测试用户",
        "avatar": "data:image/png;base64,AAAA",
        "community": "兰园", "building": "3栋", "unit": "1单元", "room": "101",
        "bio": "你好", "show_building": True, "show_room": False,
    }
    out = _format_user_profile(data)
    assert "昵称：测试用户" in out
    assert "openid" not in out
    assert "unionid" not in out
    assert "room" not in out
    assert "base64" not in out


def test_post_formatter_drops_avatar():
    """帖子 formatter 输出不含 base64 头像（作者/评论者/点赞者）"""
    data = {
        "id": 1,
        "user": {"id": 2, "nickname": "小明", "avatar": "data:image/png;base64,AAAA"},
        "content": "大家好",
        "comments": [
            {"id": 3, "user": {"nickname": "小红", "avatar": "data:image/png;base64,BBBB"},
             "content": "欢迎"},
        ],
        "likers": [{"nickname": "小刚", "avatar": "data:image/png;base64,CCCC"}],
    }
    out = _format_post(data)
    assert "大家好" in out
    assert "小明" in out
    assert "base64" not in out


def test_search_history_formatter():
    """历史搜索：anchor 内容 + 上下文窗口，anchor 不重复"""
    data = {
        "total": 1,
        "results": [{
            "message_id": 10, "role": "user", "content": "我想找昨天的聊天",
            "messages_before": 2, "messages_after": 1,
            "context_window": [
                {"message_id": 9, "role": "assistant", "content": "好的"},
                {"message_id": 10, "role": "user", "content": "我想找昨天的聊天"},
                {"message_id": 11, "role": "assistant", "content": "稍等"},
            ],
        }],
    }
    out = _format_search_history(data)
    assert "我想找昨天的聊天" in out
    assert "好的" in out
    assert out.count("我想找昨天的聊天") == 1  # anchor 不重复出现在上下文行


def test_memory_list_formatter():
    """记忆列表：id 编号即 memory_get 的索引"""
    data = [
        {"id": 1, "type": "user", "name": "昵称", "description": "喜欢简洁"},
        {"id": 2, "type": "reference", "name": "装修", "description": "关注木工"},
    ]
    out = _format_memory_list(data)
    assert "#1" in out and "#2" in out
    assert "喜欢简洁" in out

"""tool formatter 全覆盖验证（issue #68/#69）

- 所有 app.api 模块注册的 @tool 都带 result_formatter（formatter 是唯一主路径，
  不再靠全局 base64 清洗兜底）
- formatter 原则（#69 用户 review）：只做必要删减（avatar 等），不改写格式——
  输出保留原始 JSON 结构，删减项在 formatter docstring 注释声明
"""

import json
from datetime import datetime

from app.main import app  # noqa: F401,E402 全量 import 触发 @tool 注册
from app.harness.tool_registry import _to_dict, registry
from app.api.v1.ai import _format_search_history
from app.api.v1.comments import _format_list_comments
from app.api.v1.memory import _format_memory_list
from app.api.v1.notifications import _format_list_notifications
from app.api.v1.posts import _format_get_post
from app.api.v1.profile import _format_get_my_profile
from app.schemas.comment import CommentResponse
from app.schemas.common import ReplyTo, UserBrief
from app.schemas.notification import NotificationResponse


def test_all_api_tools_have_formatter():
    """app.api 下注册的 tool 全部带 result_formatter"""
    missing = [
        t.name
        for t in registry.all
        if t.fn.__module__.startswith("app.api.") and t.result_formatter is None
    ]
    assert missing == [], f"以下 tool 缺 result_formatter: {missing}"


def test_profile_formatter_hides_privacy_fields():
    """我的资料 formatter 删减 openid/unionid/房号/base64 头像，其余字段保留"""
    data = {
        "id": 1, "openid": "wx-openid", "unionid": "u-1",
        "nickname": "测试用户",
        "avatar": "data:image/png;base64,AAAA",
        "community": "兰园", "building": "3栋", "unit": "1单元", "room": "101",
        "bio": "你好", "show_building": True, "show_room": False,
    }
    parsed = json.loads(_format_get_my_profile(data))
    assert parsed["nickname"] == "测试用户"
    assert parsed["community"] == "兰园"  # 有用字段原样保留
    for key in ("avatar", "openid", "unionid", "unit", "room"):
        assert key not in parsed, f"{key} 应被删减"


def test_post_formatter_drops_avatar():
    """帖子 formatter 删减作者/评论者/点赞者 avatar，内容与评论结构保留"""
    data = {
        "id": 1,
        "user": {"id": 2, "nickname": "小明", "avatar": "data:image/png;base64,AAAA"},
        "content": "大家好",
        "comments": [
            {"id": 3, "user": {"nickname": "小红", "avatar": "data:image/png;base64,BBBB"},
             "content": "欢迎", "reply_to": None},
        ],
        "likers": [{"id": 5, "nickname": "小刚", "avatar": "data:image/png;base64,CCCC"}],
    }
    parsed = json.loads(_format_get_post(data))
    assert parsed["content"] == "大家好"
    assert parsed["user"]["nickname"] == "小明"
    assert "avatar" not in parsed["user"]
    assert "avatar" not in parsed["comments"][0]["user"]
    assert "avatar" not in parsed["likers"][0]


def test_search_history_formatter_preserves_structure():
    """search_history 无删减：命中消息结构原样返回"""
    data = {
        "total": 1,
        "results": [{
            "message_id": 10, "role": "user", "content": "我想找昨天的聊天",
            "messages_before": 2, "messages_after": 1,
            "context_window": [
                {"message_id": 9, "role": "assistant", "content": "好的"},
                {"message_id": 11, "role": "assistant", "content": "稍等"},
            ],
        }],
    }
    parsed = json.loads(_format_search_history(data))
    assert parsed["total"] == 1
    r = parsed["results"][0]
    assert r["content"] == "我想找昨天的聊天"
    assert len(r["context_window"]) == 2
    assert r["messages_before"] == 2 and r["messages_after"] == 1


def test_memory_list_formatter_strips_body():
    """记忆列表删减 body（全文），元数据保留；全文用 memory_get 按 id 取"""
    data = [
        {"id": 1, "type": "user", "name": "昵称", "description": "喜欢简洁",
         "body": "很长很长的正文……", "created_at": "2026-08-01T10:00:00"},
    ]
    parsed = json.loads(_format_memory_list(data))
    assert parsed[0]["id"] == 1
    assert parsed[0]["description"] == "喜欢简洁"
    assert "body" not in parsed[0]


def test_comment_list_formatter_with_real_models():
    """真实形态：list[CommentResponse] 走 _to_dict → formatter 全链路（#69 review）

    回归：_to_dict 对顶层 list 递归转换，formatter 收到 dict 列表而非 Pydantic 对象。
    """
    data = [
        CommentResponse(
            id=1,
            user=UserBrief(id=2, nickname="小明", avatar="data:image/png;base64,AAAA"),
            content="欢迎新邻居",
            reply_to=None,
            created_at=datetime(2026, 8, 10, 10, 0, 0),
        ),
        CommentResponse(
            id=3,
            user=UserBrief(id=4, nickname="小红", avatar="data:image/png;base64,BBBB"),
            content="谢谢",
            reply_to=ReplyTo(user_id=2, nickname="小明"),
            created_at=datetime(2026, 8, 10, 10, 5, 0),
        ),
    ]
    converted = _to_dict(data)
    assert all(isinstance(item, dict) for item in converted), (
        f"_to_dict 未递归 list，formatter 会收到 Pydantic 对象: {converted!r}"
    )
    out = _format_list_comments(converted)
    parsed = json.loads(out)
    assert len(parsed) == 2
    assert parsed[0]["user"]["nickname"] == "小明"
    assert "avatar" not in parsed[0]["user"]  # 删减生效
    assert parsed[1]["reply_to"]["nickname"] == "小明"  # reply_to 嵌套结构保留


def test_list_notifications_formatter_with_real_models():
    """真实形态：list[NotificationResponse] 走 _to_dict → formatter 全链路（#69 review）"""
    data = [
        NotificationResponse(
            id=1,
            type="like",
            from_user=UserBrief(id=2, nickname="小明", avatar="data:image/png;base64,AAAA"),
            post_id=10,
            post_title="大家好",
            comment_id=None,
            is_read=False,
            created_at=datetime(2026, 8, 10, 10, 0, 0),
        ),
    ]
    converted = _to_dict(data)
    assert isinstance(converted[0], dict)
    out = _format_list_notifications(converted)
    parsed = json.loads(out)
    assert parsed[0]["type"] == "like"
    assert parsed[0]["from_user"]["nickname"] == "小明"
    assert "avatar" not in parsed[0]["from_user"]

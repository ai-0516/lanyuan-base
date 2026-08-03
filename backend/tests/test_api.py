"""兰园公共底座 API 测试"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.core.database import init_db
from app.harness.hooks import events as hook_events


@pytest.fixture(autouse=True)
async def setup_db():
    """每个测试前后清理数据库"""
    hook_events.reset()
    await _clear_db()
    await init_db()
    yield
    await _clear_db()


async def _clear_db():
    """清理所有表数据（使用 engine session 避免额外连接锁）"""
    from app.core.database import async_session_factory
    from sqlalchemy import text
    try:
        async with async_session_factory() as session:
            for t in [
                "user_memories", "messages", "conversations", "notifications",
                "likes", "comments", "posts", "users",
            ]:
                await session.execute(text(f"DELETE FROM {t}"))
            await session.commit()
    except Exception:
        pass


@pytest.fixture
async def client():
    """Async HTTP 客户端"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def auth_token(client: AsyncClient) -> str:
    """登录获取 token"""
    response = await client.post("/api/v1/auth/login", json={"code": "test_user_001"})
    data = response.json()
    return data["data"]["token"]


@pytest.fixture
async def auth_headers(auth_token: str) -> dict:
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    """测试健康检查"""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    assert data["data"]["status"] == "ok"


@pytest.mark.asyncio
async def test_login(client: AsyncClient):
    """测试登录"""
    response = await client.post("/api/v1/auth/login", json={"code": "test_user_abc"})
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    assert "token" in data["data"]
    assert "user" in data["data"]
    assert data["data"]["user"]["nickname"] == "兰园业主"


@pytest.mark.asyncio
async def test_login_same_user(client: AsyncClient):
    """同个 code 登录返回同一用户"""
    resp1 = await client.post("/api/v1/auth/login", json={"code": "same_user"})
    resp2 = await client.post("/api/v1/auth/login", json={"code": "same_user"})
    assert resp1.json()["data"]["user"]["id"] == resp2.json()["data"]["user"]["id"]


@pytest.mark.asyncio
async def test_auth_check(client: AsyncClient, auth_headers: dict):
    """测试 token 验证"""
    response = await client.get("/api/v1/auth/check", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    assert data["data"]["valid"] is True


@pytest.mark.asyncio
async def test_auth_check_unauthorized(client: AsyncClient):
    """未认证请求返回 401"""
    response = await client.get("/api/v1/auth/check")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_post(client: AsyncClient, auth_headers: dict):
    """测试发布帖子"""
    response = await client.post(
        "/api/v1/posts",
        json={"content": "测试帖子内容", "images": []},
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    post = data["data"]
    assert post["content"] == "测试帖子内容"
    assert post["user"]["nickname"] == "兰园业主"


@pytest.mark.asyncio
async def test_get_posts(client: AsyncClient, auth_headers: dict):
    """测试帖子列表"""
    # 先创建帖子
    await client.post(
        "/api/v1/posts",
        json={"content": "帖子1", "images": []},
        headers=auth_headers,
    )
    await client.post(
        "/api/v1/posts",
        json={"content": "帖子2", "images": []},
        headers=auth_headers,
    )

    response = await client.get("/api/v1/posts?page=1&size=20", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    posts = data["data"]
    assert posts["total"] == 2
    assert len(posts["items"]) == 2
    # 时间倒序
    assert posts["items"][0]["content"] == "帖子2"


@pytest.mark.asyncio
async def test_delete_post(client: AsyncClient, auth_headers: dict):
    """测试删除帖子"""
    # 创建
    create_resp = await client.post(
        "/api/v1/posts",
        json={"content": "待删除", "images": []},
        headers=auth_headers,
    )
    post_id = create_resp.json()["data"]["id"]

    # 删除
    del_resp = await client.delete(f"/api/v1/posts/{post_id}", headers=auth_headers)
    assert del_resp.status_code == 200

    # 验证已删除
    list_resp = await client.get("/api/v1/posts", headers=auth_headers)
    assert list_resp.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_like_post(client: AsyncClient, auth_headers: dict):
    """测试点赞/取消点赞"""
    # 创建帖子
    post_resp = await client.post(
        "/api/v1/posts",
        json={"content": "点赞测试", "images": []},
        headers=auth_headers,
    )
    post_id = post_resp.json()["data"]["id"]

    # 点赞
    like_resp = await client.post(f"/api/v1/posts/{post_id}/like", headers=auth_headers)
    assert like_resp.status_code == 200
    assert like_resp.json()["data"]["liked"] is True

    # 取消点赞
    unlike_resp = await client.post(f"/api/v1/posts/{post_id}/like", headers=auth_headers)
    assert unlike_resp.json()["data"]["liked"] is False


@pytest.mark.asyncio
async def test_comment(client: AsyncClient, auth_headers: dict):
    """测试评论"""
    # 创建帖子
    post_resp = await client.post(
        "/api/v1/posts",
        json={"content": "评论测试", "images": []},
        headers=auth_headers,
    )
    post_id = post_resp.json()["data"]["id"]

    # 评论
    comment_resp = await client.post(
        f"/api/v1/posts/{post_id}/comments",
        json={"content": "测试评论内容"},
        headers=auth_headers,
    )
    assert comment_resp.status_code == 200
    data = comment_resp.json()
    assert data["code"] == 0
    comment = data["data"]
    assert comment["content"] == "测试评论内容"
    assert comment["user"]["nickname"] == "兰园业主"

    # 验证评论出现在帖子列表中
    list_resp = await client.get("/api/v1/posts", headers=auth_headers)
    assert len(list_resp.json()["data"]["items"][0]["comments"]) == 1


@pytest.mark.asyncio
async def test_reply_comment(client: AsyncClient, auth_headers: dict):
    """测试回复评论"""
    # 创建帖子
    post_resp = await client.post(
        "/api/v1/posts",
        json={"content": "回复测试", "images": []},
        headers=auth_headers,
    )
    post_id = post_resp.json()["data"]["id"]

    # 先创建评论
    comment_resp = await client.post(
        f"/api/v1/posts/{post_id}/comments",
        json={"content": "原始评论"},
        headers=auth_headers,
    )
    comment_id = comment_resp.json()["data"]["id"]

    # 回复评论
    reply_resp = await client.post(
        f"/api/v1/posts/{post_id}/comments",
        json={"content": "回复内容", "parent_comment_id": comment_id},
        headers=auth_headers,
    )
    assert reply_resp.status_code == 200
    data = reply_resp.json()
    assert data["code"] == 0
    reply = data["data"]
    assert reply["reply_to"] is not None
    assert reply["reply_to"]["nickname"] == "兰园业主"


@pytest.mark.asyncio
async def test_notifications(client: AsyncClient, auth_headers: dict, auth_token: str):
    """测试通知系统"""
    # 创建帖子（用户A）
    post_resp = await client.post(
        "/api/v1/posts",
        json={"content": "通知测试", "images": []},
        headers=auth_headers,
    )
    post_id = post_resp.json()["data"]["id"]

    # 用另一个用户点赞
    login_b = await client.post("/api/v1/auth/login", json={"code": "user_b"})
    headers_b = {"Authorization": f"Bearer {login_b.json()['data']['token']}"}
    await client.post(f"/api/v1/posts/{post_id}/like", headers=headers_b)

    # 原用户查看通知
    notif_resp = await client.get("/api/v1/notifications", headers=auth_headers)
    body = notif_resp.json()
    assert body["code"] == 0
    notifications = body["data"]
    assert len(notifications) > 0
    assert notifications[0]["type"] == "like"

    # 查看未读数量
    count_resp = await client.get("/api/v1/notifications/count", headers=auth_headers)
    assert count_resp.json()["data"]["count"] > 0


@pytest.mark.asyncio
async def test_profile_update(client: AsyncClient, auth_headers: dict):
    """测试更新个人资料"""
    update_resp = await client.put(
        "/api/v1/user/me",
        json={"nickname": "测试用户", "bio": "我的签名", "community": "东方兰园"},
        headers=auth_headers,
    )
    assert update_resp.status_code == 200
    data = update_resp.json()
    assert data["code"] == 0
    user = data["data"]
    assert user["nickname"] == "测试用户"
    assert user["bio"] == "我的签名"

    # 验证持久化
    get_resp = await client.get("/api/v1/user/me", headers=auth_headers)
    assert get_resp.json()["data"]["nickname"] == "测试用户"


@pytest.mark.asyncio
async def test_ai_session(client: AsyncClient, auth_headers: dict):
    """测试 AI 会话创建"""
    response = await client.post("/api/v1/ai/session", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["code"] == 0
    assert "session_id" in data["data"]
    assert "messages" in data["data"]


@pytest.mark.asyncio
async def test_ai_chat(client: AsyncClient, auth_headers: dict):
    """测试 AI 对话（SSE 流式）"""
    # 获取 session
    session_resp = await client.post("/api/v1/ai/session", headers=auth_headers)
    session_id = session_resp.json()["data"]["session_id"]

    # 发消息
    chat_resp = await client.post(
        "/api/v1/ai/chat",
        json={"session_id": session_id, "message": "你好"},
        headers=auth_headers,
    )
    assert chat_resp.status_code == 200
    # 验证 SSE 事件
    assert "event: token" in chat_resp.text or "event:" in chat_resp.text


@pytest.mark.asyncio
async def test_get_user_public(client: AsyncClient, auth_headers: dict):
    """测试查看用户公开信息"""
    # 获取自己
    me_resp = await client.get("/api/v1/user/me", headers=auth_headers)
    my_id = me_resp.json()["data"]["id"]

    # 查看公开信息
    public_resp = await client.get(f"/api/v1/users/{my_id}", headers=auth_headers)
    assert public_resp.status_code == 200
    data = public_resp.json()
    assert data["code"] == 0
    user = data["data"]
    assert "id" in user
    assert "nickname" in user


# ═══════════════════════════════════════════
#  统一错误响应格式（#27）
# ═══════════════════════════════════════════

@pytest.mark.asyncio
async def test_unauthorized_unified_format(client: AsyncClient):
    """未登录 401 响应为统一 {code, message} 格式，而非 {detail: ...}（#27）

    无 token 时走 HTTPBearer 默认 401（detail 为字符串 "Not authenticated"），
    统一后为 code=40100；带无效 token 的 40101/40102 同理均为顶层 code。
    """
    response = await client.get("/api/v1/posts")
    assert response.status_code == 401
    data = response.json()
    assert "detail" not in data
    assert data["code"] == 40100
    assert data["message"] == "Not authenticated"


@pytest.mark.asyncio
async def test_404_route_unified_format(client: AsyncClient):
    """路由不存在 404 响应为统一 {code, message} 格式（#27）"""
    response = await client.get("/api/v1/this-route-does-not-exist")
    assert response.status_code == 404
    data = response.json()
    assert "detail" not in data
    assert data["code"] == 40400
    assert data["message"] == "Not Found"


@pytest.mark.asyncio
async def test_422_validation_unified_format(client: AsyncClient, auth_headers: dict):
    """请求校验失败 422 响应为统一格式（#27）"""
    response = await client.post("/api/v1/posts", json={}, headers=auth_headers)
    assert response.status_code == 422
    data = response.json()
    assert "detail" not in data
    assert data["code"] == 42200
    assert data["message"] == "Field required"


@pytest.mark.asyncio
async def test_like_nonexistent_post_business_error(client: AsyncClient, auth_headers: dict):
    """点赞不存在的帖子返回业务错误 40401，而非 500（#28）"""
    response = await client.post("/api/v1/posts/99999/like", headers=auth_headers)
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == 40401
    assert data["message"] == "帖子不存在"


# ═══════════════════════════════════════════
#  跨会话记忆 API（#9）
# ═══════════════════════════════════════════

@pytest.mark.asyncio
async def test_memory_crud(client: AsyncClient, auth_headers: dict):
    """记忆增删查"""
    # 初始为空
    resp = await client.get("/api/v1/memory", headers=auth_headers)
    assert resp.json()["code"] == 0
    assert resp.json()["data"] == []

    # 添加
    resp = await client.post(
        "/api/v1/memory",
        json={
            "name": "user-name",
            "type": "user",
            "description": "用户名字",
            "body": "我叫张三",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    mem = resp.json()["data"]
    assert mem["name"] == "user-name"
    mem_id = mem["id"]

    # 列表包含
    resp = await client.get("/api/v1/memory", headers=auth_headers)
    assert len(resp.json()["data"]) == 1

    # 删除
    resp = await client.delete(f"/api/v1/memory/{mem_id}", headers=auth_headers)
    assert resp.json()["data"]["deleted"] is True

    # 删除后为空
    resp = await client.get("/api/v1/memory", headers=auth_headers)
    assert resp.json()["data"] == []


@pytest.mark.asyncio
async def test_memory_add_invalid_type(client: AsyncClient, auth_headers: dict):
    """非法 type 返回业务错误 40011"""
    resp = await client.post(
        "/api/v1/memory",
        json={
            "name": "x",
            "type": "feedback",
            "description": "d",
            "body": "b",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == 40011


@pytest.mark.asyncio
async def test_memory_user_isolation_api(client: AsyncClient, auth_headers: dict):
    """API 层用户隔离：B 看不到 A 的记忆"""
    # A 添加记忆
    await client.post(
        "/api/v1/memory",
        json={"name": "a-secret", "type": "user",
              "description": "A的秘密", "body": "AAA"},
        headers=auth_headers,
    )
    # B 登录（不同 code）
    login_b = await client.post("/api/v1/auth/login", json={"code": "test_user_002"})
    headers_b = {"Authorization": f"Bearer {login_b.json()['data']['token']}"}

    resp = await client.get("/api/v1/memory", headers=headers_b)
    assert resp.json()["code"] == 0
    assert resp.json()["data"] == []


@pytest.mark.asyncio
async def test_memory_delete_other_users_memory(client: AsyncClient, auth_headers: dict):
    """B 不能删除 A 的记忆（返回 deleted=false）"""
    resp = await client.post(
        "/api/v1/memory",
        json={"name": "a-mem", "type": "user",
              "description": "A的", "body": "AAA"},
        headers=auth_headers,
    )
    mem_id = resp.json()["data"]["id"]

    login_b = await client.post("/api/v1/auth/login", json={"code": "test_user_003"})
    headers_b = {"Authorization": f"Bearer {login_b.json()['data']['token']}"}

    resp = await client.delete(f"/api/v1/memory/{mem_id}", headers=headers_b)
    assert resp.json()["data"]["deleted"] is False


@pytest.mark.asyncio
async def test_memory_delete_nonexistent_success(client: AsyncClient, auth_headers: dict):
    """review #8/#12：删除不存在的记忆 id → 成功（code=0），deleted=false"""
    resp = await client.delete("/api/v1/memory/999999", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["deleted"] is False



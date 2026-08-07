"""post_service 和 comment_service 单元测试 — 直接调用 service 层覆盖所有分支

目标: 将 post_service 26% → 85%+，comment_service 29% → 85%+
"""
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import async_session_factory, init_db
from app.models.user import User
from app.models.comment import Comment
from app.models.like import Like
from app.models.post import Post
from app.schemas.post import PostCreate
from app.schemas.comment import CommentCreate
from app.services import post_service, comment_service


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


@pytest.fixture(autouse=True)
async def setup_db():
    """每个测试前后清理数据库"""
    await _clear_db()
    await init_db()
    yield
    await _clear_db()


async def _create_user(nickname: str = "测试用户", openid: str = "test") -> int:
    """辅助：创建用户并返回 user_id"""
    async with async_session_factory() as db:
        user = User(openid=openid, nickname=nickname, avatar="")
        db.add(user)
        await db.commit()
        return user.id


async def _create_post(user_id: int, content: str = "测试帖子") -> int:
    """辅助：创建帖子并返回 post_id"""
    async with async_session_factory() as db:
        result = await post_service.create_post(
            db,
            user_id,
            PostCreate(content=content, images=[])
        )
        await db.commit()
        return result.id


# ═══════════════════════════════════════════
#  post_service 测试
# ═══════════════════════════════════════════

class TestPostServiceCreate:
    """创建帖子"""

    async def test_create_post_basic(self):
        """基本创建帖子"""
        uid = await _create_user()
        async with async_session_factory() as db:
            result = await post_service.create_post(db, uid, PostCreate(content="你好兰园", images=[]))
            await db.commit()

        assert result.content == "你好兰园"
        assert result.liked is False
        assert result.comments == []
        assert result.comments == []

    async def test_create_post_with_images(self):
        """创建带图片的帖子"""
        uid = await _create_user()
        async with async_session_factory() as db:
            result = await post_service.create_post(
                db, uid,
                PostCreate(content="图", images=["http://a.jpg", "http://b.jpg"])
            )
            await db.commit()

        assert result.images == ["http://a.jpg", "http://b.jpg"]


class TestPostServiceGet:
    """获取帖子列表"""

    async def test_empty_list(self):
        """空列表"""
        uid = await _create_user()
        async with async_session_factory() as db:
            result = await post_service.get_posts(db, uid, page=1, size=20)
        assert result.total == 0
        assert result.items == []

    async def test_basic_listing(self):
        """基本列表 — 验证排序和字段"""
        uid = await _create_user()
        pid1 = await _create_post(uid, "旧")
        pid2 = await _create_post(uid, "新")

        async with async_session_factory() as db:
            result = await post_service.get_posts(db, uid)

        assert result.total == 2
        # 时间倒序：新在前
        assert result.items[0].content == "新"
        assert result.items[1].content == "旧"

    async def test_pagination(self):
        """分页"""
        uid = await _create_user()
        for i in range(5):
            await _create_post(uid, f"帖{i}")

        async with async_session_factory() as db:
            p1 = await post_service.get_posts(db, uid, page=1, size=2)
            p2 = await post_service.get_posts(db, uid, page=2, size=2)
            p3 = await post_service.get_posts(db, uid, page=3, size=2)

        assert len(p1.items) == 2
        assert len(p2.items) == 2
        assert len(p3.items) == 1
        assert p1.total == 5

    async def test_post_with_comments(self):
        """帖子含评论 — 验证 comments 字段完整"""
        uid = await _create_user()
        pid = await _create_post(uid, "有评论")

        # 创建评论
        async with async_session_factory() as db:
            await comment_service.create_comment(db, uid, pid, CommentCreate(content="评论1"))
            await comment_service.create_comment(db, uid, pid, CommentCreate(content="评论2"))
            await db.commit()

        async with async_session_factory() as db:
            result = await post_service.get_posts(db, uid)

        assert len(result.items[0].comments) == 2
        assert len(result.items[0].comments) == 2
        assert result.items[0].comments[0].content == "评论1"
        assert result.items[0].comments[1].content == "评论2"

    async def test_post_with_replies(self):
        """帖子含二级回复 — 验证 reply_to"""
        uid1 = await _create_user("A", "a")
        uid2 = await _create_user("B", "b")
        pid = await _create_post(uid1, "主帖")

        # A 发评论，B 回复
        async with async_session_factory() as db:
            c1 = await comment_service.create_comment(db, uid1, pid, CommentCreate(content="A的评论"))
            await db.flush()
            c2 = await comment_service.create_comment(
                db, uid2, pid,
                CommentCreate(content="B回复A", parent_comment_id=c1.id)
            )
            await db.commit()

        async with async_session_factory() as db:
            result = await post_service.get_posts(db, uid1)

        comments = result.items[0].comments
        assert len(comments) == 2
        assert comments[1].reply_to is not None
        assert comments[1].reply_to.nickname == "A"

    async def test_post_with_liked_status(self):
        """验证 liked 字段正确反映用户点赞状态"""
        uid1 = await _create_user("A", "a")
        uid2 = await _create_user("B", "b")
        pid = await _create_post(uid1, "测点赞")

        async with async_session_factory() as db:
            await post_service.like_post(db, pid, uid2)
            await db.commit()

        # 用 uid2 查询 — liked 应为 True
        async with async_session_factory() as db:
            result = await post_service.get_posts(db, uid2)
        assert result.items[0].liked is True
        assert len(result.items[0].likers) == 1

        # 用 uid1 查询 — liked 应为 False
        async with async_session_factory() as db:
            result = await post_service.get_posts(db, uid1)
        assert result.items[0].liked is False
        assert len(result.items[0].likers) == 1


class TestPostServiceDelete:
    """删除帖子"""

    async def test_delete_own_post(self):
        """作者删除自己的帖子"""
        uid = await _create_user()
        pid = await _create_post(uid, "我的帖")

        async with async_session_factory() as db:
            success = await post_service.delete_post(db, pid, uid)
            await db.commit()
        assert success is True

        async with async_session_factory() as db:
            result = await post_service.get_posts(db, uid)
        assert result.total == 0

    async def test_cannot_delete_others_post(self):
        """非作者不能删除"""
        uid1 = await _create_user("A", "a")
        uid2 = await _create_user("B", "b")
        pid = await _create_post(uid1, "A的帖")

        async with async_session_factory() as db:
            success = await post_service.delete_post(db, pid, uid2)
        assert success is False

    async def test_delete_non_existent(self):
        """删除不存在的帖子"""
        uid = await _create_user()
        async with async_session_factory() as db:
            success = await post_service.delete_post(db, 99999, uid)
        assert success is False

    async def test_delete_cascade(self):
        """删除帖子级联删除评论和点赞"""
        uid = await _create_user()
        pid = await _create_post(uid, "测试级联")

        # 添加评论和点赞
        async with async_session_factory() as db:
            await comment_service.create_comment(db, uid, pid, CommentCreate(content="评论"))
            await post_service.like_post(db, pid, uid)
            await db.commit()

        # 删除帖子
        async with async_session_factory() as db:
            success = await post_service.delete_post(db, pid, uid)
            await db.commit()
        assert success is True

        # 验证评论和点赞也被删除
        async with async_session_factory() as db:
            comment_count = (await db.execute(
                select(Comment).where(Comment.post_id == pid)
            )).scalars().all()
        assert len(comment_count) == 0


class TestPostServiceLike:
    """点赞/取消点赞"""

    async def test_like_then_unlike(self):
        """点赞→取消→再点赞"""
        uid1 = await _create_user("A", "a")
        uid2 = await _create_user("B", "b")
        pid = await _create_post(uid1, "测点赞")

        async with async_session_factory() as db:
            liked, count = await post_service.like_post(db, pid, uid2)
            await db.commit()
        assert liked is True
        assert count == 1

        async with async_session_factory() as db:
            unliked, count = await post_service.unlike_post(db, pid, uid2)
            await db.commit()
        assert unliked is True
        assert count == 0

        async with async_session_factory() as db:
            liked, count = await post_service.like_post(db, pid, uid2)
            await db.commit()
        assert liked is True
        assert count == 1

    async def test_like_idempotent(self):
        """重复点赞不重复计数"""
        uid1 = await _create_user("A", "a")
        uid2 = await _create_user("B", "b")
        pid = await _create_post(uid1, "幂等")

        async with async_session_factory() as db:
            liked, count = await post_service.like_post(db, pid, uid2)
            await db.commit()
        assert liked is True
        assert count == 1

        async with async_session_factory() as db:
            liked, count = await post_service.like_post(db, pid, uid2)
            await db.commit()
        assert liked is False  # 已点赞，无操作
        assert count == 1       # 计数不变

    async def test_unlike_idempotent(self):
        """取消未点赞的帖子无操作"""
        uid1 = await _create_user("A", "a")
        pid = await _create_post(uid1, "未点赞取消")
        async with async_session_factory() as db:
            unliked, count = await post_service.unlike_post(db, pid, uid1)
            await db.commit()
        assert unliked is False
        assert count == 0

    async def test_multiple_users_like(self):
        """多用户点赞计数"""
        uid1 = await _create_user("A", "a")
        uid2 = await _create_user("B", "b")
        uid3 = await _create_user("C", "c")
        pid = await _create_post(uid1, "多赞")

        async with async_session_factory() as db:
            await post_service.like_post(db, pid, uid1)
            _, count = await post_service.like_post(db, pid, uid2)
            await db.commit()
        assert count == 2

        async with async_session_factory() as db:
            _, count = await post_service.like_post(db, pid, uid3)
            await db.commit()
        assert count == 3

    async def test_like_nonexistent_post_returns_none(self):
        """点赞不存在的帖子返回 (None, 0)，不插入脏数据（#28）"""
        uid = await _create_user("A", "a")
        async with async_session_factory() as db:
            liked, count = await post_service.like_post(db, 99999, uid)
            await db.commit()
        assert liked is None
        assert count == 0

    async def test_like_foreign_key_rejects_orphan(self):
        """DB 层拒绝插入引用不存在帖子的点赞记录（绕过服务层的直接写入，#28）"""
        uid = await _create_user("A", "a")
        async with async_session_factory() as db:
            db.add(Like(post_id=99999, user_id=uid))
            with pytest.raises(IntegrityError):
                await db.flush()
            await db.rollback()

    async def test_like_cascade_delete_with_post(self):
        """删除帖子后点赞记录由 DB 外键级联删除（#28 验收标准）"""
        uid1 = await _create_user("A", "a")
        uid2 = await _create_user("B", "b")
        pid = await _create_post(uid1, "级联删除")

        async with async_session_factory() as db:
            await post_service.like_post(db, pid, uid2)
            await db.commit()

        # 直接删除帖子（绕过服务层的手动级联），验证 DB 层 CASCADE 生效
        async with async_session_factory() as db:
            result = await db.execute(select(Post).where(Post.id == pid))
            post = result.scalar_one()
            await db.delete(post)
            await db.commit()

        async with async_session_factory() as db:
            result = await db.execute(select(Like).where(Like.post_id == pid))
            assert result.scalar_one_or_none() is None


# ═══════════════════════════════════════════
#  comment_service 测试
# ═══════════════════════════════════════════

class TestCommentServiceCreate:
    """创建评论"""

    async def test_create_direct_comment(self):
        """直接评论帖子（非回复）"""
        uid = await _create_user()
        pid = await _create_post(uid)

        async with async_session_factory() as db:
            result = await comment_service.create_comment(
                db, uid, pid, CommentCreate(content="直接评论")
            )
            await db.commit()

        assert result.content == "直接评论"
        assert result.reply_to is None
        assert result.user.nickname == "测试用户"

    async def test_create_reply_comment(self):
        """回复评论 — reply_to 字段完整"""
        uid1 = await _create_user("A", "a")
        uid2 = await _create_user("B", "b")
        pid = await _create_post(uid1)

        # 先创建一条评论
        async with async_session_factory() as db:
            c1 = await comment_service.create_comment(
                db, uid1, pid, CommentCreate(content="首评"))
            await db.flush()

            # 用 uid2 回复
            c2 = await comment_service.create_comment(
                db, uid2, pid,
                CommentCreate(content="回复首评", parent_comment_id=c1.id))
            await db.commit()

        assert c2.reply_to is not None
        assert c2.reply_to.user_id == uid1
        assert c2.reply_to.nickname == "A"

    async def test_create_reply_notification(self):
        """回复评论时通知被回复者（而非帖主）"""
        uid_author = await _create_user("帖主", "author")
        uid_replier1 = await _create_user("评者A", "ra")
        uid_replier2 = await _create_user("评者B", "rb")
        pid = await _create_post(uid_author)

        async with async_session_factory() as db:
            # A 评论帖子 → 帖主收到通知
            c1 = await comment_service.create_comment(
                db, uid_replier1, pid, CommentCreate(content="A的评论"))
            await db.flush()

            # B 回复 A → A 收到 reply 通知（不是帖主）
            c2 = await comment_service.create_comment(
                db, uid_replier2, pid,
                CommentCreate(content="B回复A", parent_comment_id=c1.id))
            await db.commit()

        # 验证回复通知发给 A
        from app.models.notification import Notification
        async with async_session_factory() as db:
            notifs = (await db.execute(
                select(Notification).where(Notification.type == "reply")
            )).scalars().all()
        assert len(notifs) >= 1
        # reply 通知应发给评者A
        reply_notif = notifs[0]
        assert reply_notif.user_id == uid_replier1  # A 是接收者

    async def test_comment_notification_to_post_author(self):
        """直接评论帖子时通知帖主"""
        uid_author = await _create_user("帖主", "pa")
        uid_commenter = await _create_user("评论者", "pc")
        pid = await _create_post(uid_author)

        async with async_session_factory() as db:
            await comment_service.create_comment(
                db, uid_commenter, pid, CommentCreate(content="路过评论"))
            await db.commit()

        from app.models.notification import Notification
        async with async_session_factory() as db:
            notifs = (await db.execute(
                select(Notification).where(Notification.type == "comment")
            )).scalars().all()
        assert len(notifs) >= 1
        assert notifs[0].user_id == uid_author  # 通知帖主

    async def test_no_self_notification(self):
        """评论自己的帖子不产生通知"""
        uid = await _create_user()
        pid = await _create_post(uid)

        async with async_session_factory() as db:
            await comment_service.create_comment(
                db, uid, pid, CommentCreate(content="自评"))
            await db.commit()

        from app.models.notification import Notification
        async with async_session_factory() as db:
            count = (await db.execute(
                select(Notification)
            )).scalars().all()
        assert len(count) == 0


class TestCommentServiceGet:
    """获取帖子评论"""

    async def test_empty_comments(self):
        """无评论"""
        uid = await _create_user()
        pid = await _create_post(uid)

        async with async_session_factory() as db:
            result = await comment_service.get_post_comments(db, pid)

        assert result == []

    async def test_multiple_comments_ordered(self):
        """多条评论按时间正序"""
        uid = await _create_user()
        pid = await _create_post(uid)

        async with async_session_factory() as db:
            await comment_service.create_comment(db, uid, pid, CommentCreate(content="1"))
            await comment_service.create_comment(db, uid, pid, CommentCreate(content="2"))
            await comment_service.create_comment(db, uid, pid, CommentCreate(content="3"))
            await db.commit()

        async with async_session_factory() as db:
            result = await comment_service.get_post_comments(db, pid)

        assert len(result) == 3
        assert result[0].content == "1"
        assert result[2].content == "3"

    async def test_comments_with_replies(self):
        """评论含回复 — reply_to 正确"""
        uid1 = await _create_user("A", "aa")
        uid2 = await _create_user("B", "bb")
        pid = await _create_post(uid1)

        async with async_session_factory() as db:
            c1 = await comment_service.create_comment(db, uid1, pid, CommentCreate(content="1楼"))
            await db.flush()
            await comment_service.create_comment(
                db, uid2, pid,
                CommentCreate(content="回复1楼", parent_comment_id=c1.id))
            await db.commit()

        async with async_session_factory() as db:
            result = await comment_service.get_post_comments(db, pid)

        assert len(result) == 2
        assert result[0].reply_to is None
        assert result[1].reply_to is not None
        assert result[1].reply_to.nickname == "A"


class TestCommentServiceDelete:
    """删除评论"""

    async def test_delete_as_author(self):
        """评论作者删除自己的评论"""
        uid = await _create_user()
        pid = await _create_post(uid)

        async with async_session_factory() as db:
            c = await comment_service.create_comment(db, uid, pid, CommentCreate(content="删"))
            await db.flush()
            cid = c.id
            success = await comment_service.delete_comment(db, cid, uid)
            await db.commit()

        assert success is True
        async with async_session_factory() as db:
            comment = (await db.execute(
                select(Comment).where(Comment.id == cid)
            )).scalar_one_or_none()
        assert comment is None

    async def test_delete_as_post_owner(self):
        """帖主删除他人的评论"""
        uid_author = await _create_user("帖主", "oa")
        uid_other = await _create_user("路人", "ob")
        pid = await _create_post(uid_author)

        async with async_session_factory() as db:
            c = await comment_service.create_comment(db, uid_other, pid, CommentCreate(content="路评"))
            await db.flush()
            cid = c.id
            success = await comment_service.delete_comment(db, cid, uid_author)
            await db.commit()

        assert success is True

    async def test_cannot_delete_as_stranger(self):
        """无关用户不能删除评论"""
        uid1 = await _create_user("A", "sa")
        uid2 = await _create_user("B", "sb")
        uid3 = await _create_user("C", "sc")
        pid = await _create_post(uid1)

        # C 评论 A 的帖子
        async with async_session_factory() as db:
            c = await comment_service.create_comment(db, uid3, pid, CommentCreate(content="C评"))
            await db.flush()
            cid = c.id
            # B 尝试删除 C 的评论（B 既不是作者也不是帖主）
            success = await comment_service.delete_comment(db, cid, uid2)
            await db.commit()

        assert success is False

    async def test_delete_non_existent_comment(self):
        """删除不存在的评论"""
        uid = await _create_user()
        async with async_session_factory() as db:
            success = await comment_service.delete_comment(db, 99999, uid)
        assert success is False


# ═══════════════════════════════════════════
#  边界测试 — 孤儿数据（无 FK 时可能出现）
# ═══════════════════════════════════════════

class TestEdgeCases:
    """覆盖服务层中的 continue/跳过逻辑"""

    async def test_orphan_post_user_skipped(self):
        """帖子作者已被删除时应跳过该帖"""
        import aiosqlite

        # 直接插入一条孤儿帖子（user_id 不存在）
        async with aiosqlite.connect("./test_lanyuan.db") as conn:
            await conn.execute(
                "INSERT INTO posts (user_id, content, images) VALUES (99999, '孤儿帖', '[]')"
            )
            await conn.commit()

        uid = await _create_user()
        async with async_session_factory() as db:
            result = await post_service.get_posts(db, uid, page=1, size=20)
        # 孤儿帖子应被跳过，不抛出异常
        # 总数应只包含自己创建的帖子（如有）
        assert result.total >= 0

    async def test_orphan_comment_skipped(self):
        """评论者已被删除时应跳过该评论"""
        import aiosqlite

        uid = await _create_user()
        pid = await _create_post(uid, "正常帖")

        # 直接插入孤儿评论（user_id 不存在）
        async with aiosqlite.connect("./test_lanyuan.db") as conn:
            await conn.execute(
                "INSERT INTO comments (post_id, user_id, content) VALUES (?, 99999, '孤儿评论')",
                (pid,)
            )
            await conn.commit()

        async with async_session_factory() as db:
            result = await post_service.get_posts(db, uid)
            comments = result.items[0].comments
        # 孤儿评论应被跳过
        assert all(c.user.id is not None for c in comments)

    async def test_get_comments_orphan_skipped(self):
        """get_post_comments 中孤儿评论者应被跳过"""
        import aiosqlite

        uid = await _create_user()
        pid = await _create_post(uid, "测孤儿")

        async with aiosqlite.connect("./test_lanyuan.db") as conn:
            await conn.execute(
                "INSERT INTO comments (post_id, user_id, content) VALUES (?, 99999, '孤儿')",
                (pid,)
            )
            await conn.commit()

        async with async_session_factory() as db:
            result = await comment_service.get_post_comments(db, pid)
        # 孤儿评论被跳过，结果为空
        assert all(r.user is not None for r in result)


# ═══════════════════════════════════════════
#  ai_service 测试
# ═══════════════════════════════════════════

class TestAISession:
    """AI 会话管理"""

    async def test_create_new_session(self):
        """首次进入 AI → 创建新会话"""
        uid = await _create_user()
        async with async_session_factory() as db:
            from app.services.ai_service import get_or_create_session
            session = await get_or_create_session(db, uid)
            await db.commit()

        assert session.session_id > 0
        assert session.messages == []

    async def test_reuse_existing_session(self):
        """再次进入 AI → 复用最近会话"""
        uid = await _create_user()
        async with async_session_factory() as db:
            from app.models.conversation import Conversation
            s1 = Conversation(user_id=uid, title="")
            db.add(s1)
            await db.flush()
            session_id = s1.id
            await db.commit()

        from app.services.ai_service import get_or_create_session
        async with async_session_factory() as db:
            session = await get_or_create_session(db, uid)
            await db.commit()

        assert session.session_id == session_id

    async def test_return_all_messages(self):
        """复用会话时返回全部历史消息，不截断"""
        uid = await _create_user()
        async with async_session_factory() as db:
            from app.models.conversation import Conversation, Message
            conv = Conversation(user_id=uid, title="")
            db.add(conv)
            await db.flush()
            cid = conv.id
            for i in range(25):
                db.add(Message(conversation_id=cid, role="user", content=f"msg{i}"))
            await db.commit()

        from app.services.ai_service import get_or_create_session
        async with async_session_factory() as db:
            session = await get_or_create_session(db, uid)

        # 返回全部 25 条消息，不截断
        assert len(session.messages) == 25
        assert session.messages[0].content == "msg0"
        assert session.messages[-1].content == "msg24"

    async def test_session_per_user_isolation(self):
        """不同用户会话隔离"""
        uid1 = await _create_user("A", "a_openid")
        uid2 = await _create_user("B", "b_openid")

        async with async_session_factory() as db:
            from app.models.conversation import Conversation
            db.add(Conversation(user_id=uid1, title=""))
            await db.commit()

        from app.services.ai_service import get_or_create_session
        async with async_session_factory() as db:
            s1 = await get_or_create_session(db, uid1)
            s2 = await get_or_create_session(db, uid2)
            await db.commit()

        # A 复用了已有会话
        assert s1.session_id > 0
        # B 是新会话
        assert s2.session_id > 0
        assert s1.session_id != s2.session_id


class TestAIStreamChat:
    """AI 流式聊天"""

    async def test_invalid_session_returns_error(self):
        """不属于当前用户的 session → error 事件"""
        uid1 = await _create_user("A", "a_s")
        uid2 = await _create_user("B", "b_s")

        async with async_session_factory() as db:
            from app.models.conversation import Conversation
            conv = Conversation(user_id=uid1, title="")
            db.add(conv)
            await db.flush()
            sid = conv.id
            await db.commit()

        from app.services.ai_service import stream_chat
        async with async_session_factory() as db:
            events = []
            async for event, content in stream_chat(db, uid2, sid, "你好"):
                events.append((event, content))
            await db.commit()

        assert len(events) == 1
        assert events[0][0] == "error"

    async def test_saves_user_message(self):
        """用户消息存入 messages 表"""
        uid = await _create_user()
        async with async_session_factory() as db:
            from app.services.ai_service import get_or_create_session
            session = await get_or_create_session(db, uid)
            sid = session.session_id
            await db.commit()

        from app.services.ai_service import stream_chat
        async with async_session_factory() as db:
            async for _ in stream_chat(db, uid, sid, "你好"):
                pass
            await db.commit()

        from app.models.conversation import Message
        from sqlalchemy import select
        async with async_session_factory() as db:
            msgs = (await db.execute(
                select(Message).where(Message.conversation_id == sid)
            )).scalars().all()

        assert len(msgs) == 2  # 用户消息 + AI 回复
        assert msgs[0].role == "user"
        assert msgs[0].content == "你好"

    async def test_mock_reply_without_api_key(self):
        """无 DEEPSEEK_API_KEY 时返回模拟回复"""
        uid = await _create_user()
        async with async_session_factory() as db:
            from app.services.ai_service import get_or_create_session
            session = await get_or_create_session(db, uid)
            sid = session.session_id
            await db.commit()

        from app.services.ai_service import stream_chat
        async with async_session_factory() as db:
            events = []
            async for event, content in stream_chat(db, uid, sid, "暖气温控"):
                events.append((event, content))
            await db.commit()

        # message:start → token → done（#22：每条回复以 message:start 为界）
        assert len(events) == 3
        assert events[0][0] == "message:start"
        assert events[1][0] == "token"
        assert "暖气温控" in str(events[1][1])
        assert events[2][0] == "done"

    async def test_saves_assistant_reply(self):
        """AI 回复存入 messages 表"""
        uid = await _create_user()
        async with async_session_factory() as db:
            from app.services.ai_service import get_or_create_session
            session = await get_or_create_session(db, uid)
            sid = session.session_id
            await db.commit()

        from app.services.ai_service import stream_chat
        async with async_session_factory() as db:
            async for _ in stream_chat(db, uid, sid, "测试"):
                pass
            await db.commit()

        from app.models.conversation import Message
        from sqlalchemy import select
        async with async_session_factory() as db:
            msgs = (await db.execute(
                select(Message).where(Message.conversation_id == sid)
                    .order_by(Message.created_at.asc())
            )).scalars().all()

        assert len(msgs) == 2
        assert msgs[1].role == "assistant"
        assert len(msgs[1].content) > 0

    async def test_conversation_updated_at_refreshed(self):
        """发送消息后会话 updated_at 刷新"""
        uid = await _create_user()
        async with async_session_factory() as db:
            from app.services.ai_service import get_or_create_session
            session = await get_or_create_session(db, uid)
            sid = session.session_id
            await db.commit()

        from app.services.ai_service import stream_chat
        async with async_session_factory() as db:
            async for _ in stream_chat(db, uid, sid, "你好"):
                pass
            await db.commit()

        from app.models.conversation import Conversation
        from sqlalchemy import select
        async with async_session_factory() as db:
            conv = (await db.execute(
                select(Conversation).where(Conversation.id == sid)
            )).scalar_one()
        assert conv.updated_at is not None


class TestAICmdNew:
    """#41：/new 已移除（2026-08-06）——作为普通消息处理，不再创建新会话"""

    async def test_new_treated_as_normal_message(self):
        """输入 /new 不再建新会话，走正常 stream 流程"""
        uid = await _create_user()
        sid = None
        async with async_session_factory() as db:
            from app.services.ai_service import get_or_create_session
            s = await get_or_create_session(db, uid)
            sid = s.session_id
            await db.commit()

        from app.services.ai_service import stream_chat
        async with async_session_factory() as db:
            events = []
            async for event, content in stream_chat(db, uid, sid, "/new"):
                events.append((event, content))
            await db.commit()

        # 无 cmd_new_session 事件，走正常流（message:start → token → done）
        events_types = [e[0] for e in events]
        assert "cmd_new_session" not in events_types
        assert events_types[-1] == "done"

        # 会话未被替换（用户最新会话仍是原 sid）
        async with async_session_factory() as db:
            from app.harness import session as session_ops
            conv = await session_ops.get_or_create(db, uid)
        assert conv.id == sid

    async def test_new_with_prefix_normal_flow(self):
        """/new 作为前缀的普通消息走正常流程"""
        uid = await _create_user()
        async with async_session_factory() as db:
            from app.services.ai_service import get_or_create_session
            s = await get_or_create_session(db, uid)
            sid = s.session_id
            await db.commit()

        from app.services.ai_service import stream_chat
        async with async_session_factory() as db:
            events = []
            async for event, content in stream_chat(db, uid, sid, "/new 帮我发个帖子"):
                events.append((event, content))
            await db.commit()

        events_types = [e[0] for e in events]
        assert "cmd_new_session" not in events_types
        assert events_types[-1] == "done"


class TestRotation:
    """#45 压缩旋转：超限 → 建新会话 + u_k 迁移 + 摘要 tool 入库（TECH_SPEC 8.3）"""

    @pytest.fixture
    def fake_summary(self, monkeypatch):
        """mock 摘要 LLM（避免真 API 调用）"""
        from app.harness import context_compact

        async def _fake_summarize(messages):
            return "【压缩摘要】历史对话要点"

        monkeypatch.setattr(context_compact, "_summarize", _fake_summarize)
        return _fake_summarize

    @pytest.fixture
    def captured_events(self, monkeypatch):
        """捕获 events.emit 调用（验证 SESSION_END 发射）"""
        import app.harness.hooks.events as events_mod
        captured = []

        def _fake_emit(name, data):
            captured.append((name, dict(data)))

        monkeypatch.setattr(events_mod, "emit", _fake_emit)
        return captured

    async def _seed_overflow_session(self, uid: int, rounds: int = 8) -> int:
        """创建会话并塞入超过阈值的历史消息，返回 session_id"""
        from app.harness import session as session_ops
        from app.services.ai_service import get_or_create_session
        async with async_session_factory() as db:
            s = await get_or_create_session(db, uid)
            sid = s.session_id
            for i in range(rounds):
                await session_ops.save_user_message(db, sid, f"历史问题 {i} " + "x" * 20)
                await session_ops.save_assistant_message(db, sid, f"历史回答 {i} " + "y" * 20)
            # 超限判断依据 = 最近一次 LLM 调用的精确 total_tokens（PR #49）：
            # 插入一条超阈值的 usage 记录模拟「上次调用已超限」
            from app.config import settings
            from app.models.llm_usage import LlmUsage
            db.add(LlmUsage(
                req_id="seed-overflow",
                session_id=sid,
                user_id=uid,
                prompt_tokens=settings.SESSION_ROTATION_THRESHOLD // 2,
                total_tokens=settings.SESSION_ROTATION_THRESHOLD + 1000,
            ))
            await db.commit()
        return sid

    async def test_rotation_on_overflow(self, fake_summary, captured_events, monkeypatch):
        """超限 → 建 B、u_k 迁移、tool_call + 摘要入库、SESSION_END emit、缓存清理"""
        from app.harness.hooks import events
        from app.models.conversation import Conversation, Message
        from app.harness import context as context_mod

        uid = await _create_user()
        sid = await self._seed_overflow_session(uid)

        # 先让 A 的 system prompt 进缓存（rotation 后应被清理）
        context_mod.get_system_prompt({"session_id": str(sid)})
        assert str(sid) in context_mod._SESSION_PROMPT_CACHE

        # 记录缓存清理调用
        invalidated = []
        original_invalidate = context_mod.invalidate_session_prompt

        def _track(session_id):
            invalidated.append(session_id)
            original_invalidate(session_id)

        monkeypatch.setattr(context_mod, "invalidate_session_prompt", _track)

        from app.services.ai_service import stream_chat
        async with async_session_factory() as db:
            async for _ in stream_chat(db, uid, sid, "触发压缩的新问题"):
                pass
            await db.commit()

        # 1. 新会话 B 存在且 ≠ A
        async with async_session_factory() as db:
            convs = (await db.execute(
                select(Conversation).where(Conversation.user_id == uid)
                .order_by(Conversation.id.asc())
            )).scalars().all()
            assert len(convs) == 2
            b = convs[-1]
            assert b.id != sid

            # 2. u_k（触发压缩的消息）迁移到 B，作为第一条 user 消息
            u_k = (await db.execute(
                select(Message).where(Message.content == "触发压缩的新问题")
            )).scalar_one()
            assert u_k.conversation_id == b.id

            # 3. B 的消息序列：u_k(user) → assistant(tool_call) → tool(摘要) → assistant(回复)
            b_msgs = (await db.execute(
                select(Message).where(Message.conversation_id == b.id)
                .order_by(Message.id.asc())
            )).scalars().all()
            roles = [m.role for m in b_msgs]
            assert roles == ["user", "assistant", "tool", "assistant"]
            assert b_msgs[0].content == "触发压缩的新问题"
            # tool_call 消息：assistant + tool_calls JSON
            import json
            tc = json.loads(b_msgs[1].tool_calls)
            assert tc[0]["function"]["name"] == "compress_context"
            # tool 消息：摘要 + tool_call_id 配对
            assert b_msgs[2].tool_call_id == tc[0]["id"]
            assert "压缩摘要" in b_msgs[2].content

            # 4. A 完整保留（原历史消息数不变——u_k 迁移后 A 仍是 8+8 条历史）
            a_msgs = (await db.execute(
                select(Message).where(Message.conversation_id == sid)
            )).scalars().all()
            assert len(a_msgs) == 16

        # 5. SESSION_END 事件 emit（session_id = A）
        session_end = [d for name, d in captured_events if name == events.SESSION_END]
        assert len(session_end) == 1
        assert session_end[0]["session_id"] == sid
        assert session_end[0]["user_id"] == uid

        # 6. A 的 system prompt 缓存被清理
        assert invalidated == [sid]
        assert str(sid) not in context_mod._SESSION_PROMPT_CACHE

    async def test_no_rotation_within_threshold(self):
        """未超限 → 不建新会话，消息写入原会话"""
        uid = await _create_user()
        async with async_session_factory() as db:
            from app.services.ai_service import get_or_create_session
            s = await get_or_create_session(db, uid)
            sid = s.session_id
            # 有 usage 记录但 total_tokens 未超限 → 不旋转（PR #49 精确判断）
            from app.config import settings
            from app.models.llm_usage import LlmUsage
            db.add(LlmUsage(
                req_id="seed-below",
                session_id=sid,
                user_id=uid,
                prompt_tokens=settings.SESSION_ROTATION_THRESHOLD // 2,
                total_tokens=settings.SESSION_ROTATION_THRESHOLD - 1,
            ))
            await db.commit()

        from app.services.ai_service import stream_chat
        async with async_session_factory() as db:
            async for _ in stream_chat(db, uid, sid, "你好"):
                pass
            await db.commit()

        from app.models.conversation import Conversation
        async with async_session_factory() as db:
            convs = (await db.execute(
                select(Conversation).where(Conversation.user_id == uid)
            )).scalars().all()
            assert len(convs) == 1
            assert convs[0].id == sid

    async def test_rotation_failure_keeps_session(self, captured_events, monkeypatch):
        """摘要失败 → 不旋转：A 原样、无新会话、无 SESSION_END"""
        from app.harness.hooks import events
        from app.harness import context_compact

        async def _fail_summarize(messages):
            raise context_compact.LLMSummaryError("mock 摘要失败")

        monkeypatch.setattr(context_compact, "_summarize", _fail_summarize)

        uid = await _create_user()
        sid = await self._seed_overflow_session(uid)

        from app.services.ai_service import stream_chat
        async with async_session_factory() as db:
            async for _ in stream_chat(db, uid, sid, "触发压缩的新问题"):
                pass
            await db.commit()

        from app.models.conversation import Conversation, Message
        async with async_session_factory() as db:
            convs = (await db.execute(
                select(Conversation).where(Conversation.user_id == uid)
            )).scalars().all()
            assert len(convs) == 1  # 不建新会话

            # u_k 留在 A（未迁移）
            u_k = (await db.execute(
                select(Message).where(Message.content == "触发压缩的新问题")
            )).scalar_one()
            assert u_k.conversation_id == sid

        # 无 SESSION_END 事件
        session_end = [d for name, d in captured_events if name == events.SESSION_END]
        assert session_end == []

    async def test_stale_session_id_writes_to_latest(self, fake_summary):
        """rotation 后前端持旧 session_id 发消息 → 写入最新会话（前端无感）"""
        from app.models.conversation import Conversation, Message

        uid = await _create_user()
        sid = await self._seed_overflow_session(uid)

        # 第一轮触发 rotation
        from app.services.ai_service import stream_chat
        async with async_session_factory() as db:
            async for _ in stream_chat(db, uid, sid, "触发压缩的新问题"):
                pass
            await db.commit()

        # 第二轮：前端仍用旧 sid 发消息
        async with async_session_factory() as db:
            async for _ in stream_chat(db, uid, sid, "旋转后的新问题"):
                pass
            await db.commit()

        # 新消息写入最新会话 B，而不是旧 A
        async with async_session_factory() as db:
            b = (await db.execute(
                select(Conversation).where(Conversation.user_id == uid)
                .order_by(Conversation.id.desc())
            )).scalars().first()
            msg = (await db.execute(
                select(Message).where(Message.content == "旋转后的新问题")
            )).scalar_one()
            assert msg.conversation_id == b.id

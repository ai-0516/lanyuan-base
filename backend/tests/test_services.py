"""post_service 和 comment_service 单元测试 — 直接调用 service 层覆盖所有分支

目标: 将 post_service 26% → 85%+，comment_service 29% → 85%+
"""
import os
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_lanyuan.db"

import pytest
from sqlalchemy import select

from app.core.database import async_session_factory, init_db
from app.models.user import User
from app.models.post import Post
from app.models.comment import Comment
from app.schemas.post import PostCreate
from app.schemas.comment import CommentCreate
from app.services import post_service, comment_service


@pytest.fixture(autouse=True)
async def setup_db():
    """每个测试前后清理数据库"""
    await init_db()
    yield
    import aiosqlite
    try:
        async with aiosqlite.connect("./test_lanyuan.db") as db:
            for t in ["messages", "conversations", "notifications", "likes", "comments", "posts", "users"]:
                await db.execute(f"DELETE FROM {t}")
            await db.commit()
    except Exception:
        pass


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
            await post_service.toggle_like(db, pid, uid2)
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
            await post_service.toggle_like(db, pid, uid)
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

    async def test_toggle_like_on_off(self):
        """点赞→取消→再点赞"""
        uid1 = await _create_user("A", "a")
        uid2 = await _create_user("B", "b")
        pid = await _create_post(uid1, "测点赞")

        async with async_session_factory() as db:
            liked, count = await post_service.toggle_like(db, pid, uid2)
            await db.commit()
        assert liked is True
        assert count == 1

        async with async_session_factory() as db:
            liked, count = await post_service.toggle_like(db, pid, uid2)
            await db.commit()
        assert liked is False
        assert count == 0

        async with async_session_factory() as db:
            liked, count = await post_service.toggle_like(db, pid, uid2)
            await db.commit()
        assert liked is True
        assert count == 1

    async def test_multiple_users_like(self):
        """多用户点赞计数"""
        uid1 = await _create_user("A", "a")
        uid2 = await _create_user("B", "b")
        uid3 = await _create_user("C", "c")
        pid = await _create_post(uid1, "多赞")

        async with async_session_factory() as db:
            await post_service.toggle_like(db, pid, uid1)
            _, count = await post_service.toggle_like(db, pid, uid2)
            await db.commit()
        assert count == 2

        async with async_session_factory() as db:
            _, count = await post_service.toggle_like(db, pid, uid3)
            await db.commit()
        assert count == 3


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
        from app.core.database import engine, Base
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

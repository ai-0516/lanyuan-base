"""种子数据脚本 — 为开发环境创建测试用户和帖子

用法:
    cd backend && .venv/bin/python3 seed_data.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app.core.database import async_session_factory, Base, engine
from app.core.security import create_access_token
from app.models.user import User
from app.models.post import Post
from app.models.comment import Comment
from app.models.like import Like
from app.models.notification import Notification

# 测试用户数据
TEST_USERS = [
    {"nickname": "兰园业主", "openid": "test_openid_0", "community": "兰园小区", "building": "3栋", "unit": "2", "room": "1201", "bio": "爱生活爱分享"},
    {"nickname": "小区花匠", "openid": "mock_openid_dev", "community": "兰园小区", "building": "5栋", "unit": "1", "room": "803", "bio": "养花达人"},
    {"nickname": "美食达人", "openid": "test_openid_2", "community": "兰园小区", "building": "2栋", "unit": "1", "room": "605", "bio": "爱做饭"},
    {"nickname": "运动健将", "openid": "test_openid_3", "community": "兰园小区", "building": "7栋", "unit": "1", "room": "1502", "bio": "跑步游泳羽毛球"},
    {"nickname": "猫咪爱好者", "openid": "test_openid_4", "community": "兰园小区", "building": "1栋", "unit": "1", "room": "301", "bio": "家有两只猫", "show_room": True},
]

# 测试帖子数据 (user_index 指向 TEST_USERS 索引)
TEST_POSTS = [
    {"user_index": 0, "content": "今天小区樱花开了，太美了！🌸\n大家有空可以去中心花园看看，正值盛花期。", "images": [
        "https://picsum.photos/seed/sakura/600/340",
    ]},
    {"user_index": 1, "content": "温馨提示：明天上午 9 点小区喷泉广场有免费体检活动，请各位邻居踊跃参加！记得空腹来哦。", "images": []},
    {"user_index": 2, "content": "今天做了红烧肉，分享一下做法：\n1. 五花肉焯水\n2. 炒糖色\n3. 加生抽老抽料酒\n4. 小火炖 40 分钟\n超级下饭！", "images": [
        "https://picsum.photos/seed/rou1/300/300",
        "https://picsum.photos/seed/rou2/300/300",
    ]},
    {"user_index": 3, "content": "有没有邻居想一起晨跑的？每天早上 6:30 在小区南门集合，路线是绕小区 3 圈（约 5 公里）。", "images": [
        "https://picsum.photos/seed/run1/600/340",
        "https://picsum.photos/seed/run2/300/300",
        "https://picsum.photos/seed/run3/300/300",
    ]},
    {"user_index": 0, "content": "供暖季马上结束了，大家觉得今年暖气怎么样？我家温度一直在 22 度左右，很稳定。", "images": []},
    {"user_index": 4, "content": "捡到一只小橘猫🐱，在 2 号楼楼下发现的小家伙。有谁认识这是谁家的吗？看着像走丢了。", "images": [
        "https://picsum.photos/seed/cat1/300/300",
        "https://picsum.photos/seed/cat2/300/300",
        "https://picsum.photos/seed/cat3/300/300",
        "https://picsum.photos/seed/cat4/300/300",
    ]},
    {"user_index": 1, "content": "周末义务清扫活动圆满结束！感谢今天来的 8 位邻居，南门花坛已经焕然一新。下次活动欢迎大家参与！", "images": []},
    {"user_index": 2, "content": "推荐南门新开的那家包子铺，鲜肉包 2 块钱一个，皮薄馅大，早上去要排队。", "images": []},
]

# 测试评论数据 (post_index, user_index, parent_comment_index (None for direct), content)
TEST_COMMENTS = [
    # 帖子 0: 樱花
    {"post_index": 0, "user_index": 2, "parent": None, "content": "确实好看！我今天也拍了照片"},
    {"post_index": 0, "user_index": 1, "parent": 0, "content": "回头分享一下照片啊"},
    {"post_index": 0, "user_index": 3, "parent": None, "content": "明天早上跑步经过的时候去看看"},
    # 帖子 1: 体检
    {"post_index": 1, "user_index": 0, "parent": None, "content": "谢谢提醒！需要的"},
    {"post_index": 1, "user_index": 4, "parent": None, "content": "刚好想做个全面检查"},
    # 帖子 2: 红烧肉
    {"post_index": 2, "user_index": 0, "parent": None, "content": "看起来很不错，周末试试"},
    {"post_index": 2, "user_index": 3, "parent": None, "content": "控制饮食中...看着馋啊"},
    {"post_index": 2, "user_index": 0, "parent": 5, "content": "汤汁拌饭特别香！"},
    # 帖子 3: 晨跑
    {"post_index": 3, "user_index": 2, "parent": None, "content": "6:30 太早了...有没有晚上跑的组织？"},
    # 帖子 5: 橘猫
    {"post_index": 5, "user_index": 0, "parent": None, "content": "好可爱！如果找不到主人我可以领养吗？"},
    {"post_index": 5, "user_index": 1, "parent": None, "content": "看着像 3 号楼老王的猫，我帮你问问"},
]


async def seed():
    # 确保表已存在
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as db:
        # 检查是否已有数据
        from sqlalchemy import select, func
        result = await db.execute(select(func.count(User.id)))
        if result.scalar() > 0:
            print("数据库已有数据，跳过种子数据")
            return

        # 创建用户
        users = []
        for i, u in enumerate(TEST_USERS):
            user = User(
                openid=u.get("openid", f"test_openid_{i}"),
                nickname=u["nickname"],
                avatar=f"https://i.pravatar.cc/80?img={i + 1}",
                community=u["community"],
                building=u.get("building"),
                unit=u.get("unit"),
                room=u.get("room"),
                bio=u.get("bio", ""),
                show_building=u.get("show_building", True),
                show_room=u.get("show_room", False),
            )
            db.add(user)
            users.append(user)
        await db.flush()
        print(f"✅ 创建了 {len(users)} 个测试用户")

        # 创建帖子
        posts = []
        for p in TEST_POSTS:
            author = users[p["user_index"]]
            post = Post(
                user_id=author.id,
                content=p["content"],
                images=p.get("images", []),
            )
            db.add(post)
            posts.append(post)
        await db.flush()
        print(f"✅ 创建了 {len(posts)} 个测试帖子")

        # 创建评论（需 flush 获取 id，因为存在层级依赖）
        comments = []
        for c in TEST_COMMENTS:
            post = posts[c["post_index"]]
            author = users[c["user_index"]]
            parent_id = None
            raw_parent = c.get("parent")
            if raw_parent is not None:
                parent_id = comments[raw_parent].id

            comment = Comment(
                post_id=post.id,
                user_id=author.id,
                parent_comment_id=parent_id,
                content=c["content"],
            )
            db.add(comment)
            await db.flush()
            comments.append(comment)
        await db.flush()
        print(f"✅ 创建了 {len(comments)} 个测试评论")

        # 创建点赞
        like_count = 0
        # 帖子 0: 用户 2,3 点赞
        for ui in [2, 3]:
            db.add(Like(post_id=posts[0].id, user_id=users[ui].id))
            like_count += 1
        # 帖子 1: 用户 0,4 点赞
        for ui in [0, 4]:
            db.add(Like(post_id=posts[1].id, user_id=users[ui].id))
            like_count += 1
        # 帖子 2: 用户 0,1,3 点赞
        for ui in [0, 1, 3]:
            db.add(Like(post_id=posts[2].id, user_id=users[ui].id))
            like_count += 1
        # 帖子 5: 用户 0 点赞
        db.add(Like(post_id=posts[5].id, user_id=users[0].id))
        like_count += 1
        print(f"✅ 创建了 {like_count} 个测试点赞")

        await db.commit()

    # 生成 JWT token 方便测试
    print()
    print("=" * 60)
    print("测试账号 Token（7 天有效）：")
    print("=" * 60)
    for i, u in enumerate(TEST_USERS):
        token = create_access_token(user_id=i + 1)
        print(f"  {u['nickname']} (user_id={i+1}):")
        print(f"    Authorization: Bearer {token}")
    print("=" * 60)
    print("\n种子数据创建完成！")


if __name__ == "__main__":
    asyncio.run(seed())

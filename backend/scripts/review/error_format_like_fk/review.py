"""
PR #31（#27 统一错误格式 + #28 Like 外键）触发验证脚本

验证内容：
1. #27 统一错误响应格式（4 场景）：
   - 401 无 token → {"code":40100,"message":"Not authenticated"}（顶层 code，无 detail）
   - 404 不存在路由 → {"code":40400,"message":"Not Found"}（默认文案，原为 "None"）
   - 422 参数校验失败 → {"code":42200,"message":"Field required"}（专用 handler）
   - 业务错误 dict detail 透传（无效 token → 40101 无效的 Token）
2. #28 Like 外键约束（5 场景）：
   - 点赞不存在帖子 → 400 {"code":40401,"message":"帖子不存在"}（业务错误非 500）
   - 点赞真实帖子 → 200 {"liked":true,"likeCount":1}
   - 取消点赞不存在帖子 → 200 幂等
   - ORM 直插孤儿行 → IntegrityError（FK 在 ORM 连接生效，PRAGMA per-connection）
   - 服务层 delete_post 删帖 → likes 记录自动清除（CASCADE 生效）
3. 迁移 28d5e00fd033（SQLite 实测，增量验证）：
   - 先 stamp 到 c8eba06d0ef1，预置 1 合法 + 1 孤儿 like 行
   - upgrade → 孤儿被清理（合法保留）、双 FK 建立（posts/users + ON DELETE CASCADE）
   - downgrade → FK 对称回滚移除

注意：从空库全量 upgrade head 会卡在既有迁移 f468c671cbff
（avatar_column_to_text 用 MySQL 语法 ALTER COLUMN TYPE，SQLite 不支持，main 就有，
非 PR #31 引入）。因此迁移部分用增量验证（stamp → 目标版本）。

用法：
    uv run python scripts/review/error_format_like_fk/review.py
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

# 必须在 import app.* 之前设置：临时 SQLite 库（review 专用，不动 test_lanyuan.db）
_TMPDIR = tempfile.mkdtemp(prefix="review_pr31_")
_DB_PATH = Path(_TMPDIR) / "review_lanyuan.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH}"

# 先注册所有 model（Base.metadata），否则 init_db() 的 create_all 建空表
from app.main import app  # noqa: E402,F401  (触发 models 注册)

RESULTS: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, detail: str = ""):
    RESULTS.append((label, ok, detail))
    print(f"  {'✅' if ok else '❌'} {label}" + (f" — {detail}" if detail else ""))


async def _setup_db():
    from app.core.database import init_db
    await init_db()


async def _clear_db():
    from app.core.database import async_session_factory
    from sqlalchemy import text
    async with async_session_factory() as session:
        for t in ["messages", "conversations", "notifications", "likes", "comments", "posts", "users"]:
            await session.execute(text(f"DELETE FROM {t}"))
        await session.commit()


async def _login_token(client, code="review_user_001") -> str:
    resp = await client.post("/api/v1/auth/login", json={"code": code})
    return resp.json()["data"]["token"]


async def _create_post(client, headers) -> int:
    resp = await client.post(
        "/api/v1/posts",
        json={"title": "review 帖子", "content": "用于 PR #31 验证", "building": "1栋", "room": "101"},
        headers=headers,
    )
    return resp.json()["data"]["id"]


# ───────────────────────── 场景 1：统一错误格式（#27）─────────────────────────
async def verify_error_format():
    print("\n## 场景 1：统一错误响应格式（#27）")
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1.1 无 token → 401 顶层 code
        r = await client.get("/api/v1/posts")
        body = r.json()
        record(
            "401 无 token → 40100",
            r.status_code == 401 and body.get("code") == 40100
            and body.get("message") == "Not authenticated" and "detail" not in body,
            f"status={r.status_code} body={body}",
        )

        # 1.2 不存在路由 → 404 默认文案
        r = await client.get("/api/v1/this-route-does-not-exist")
        body = r.json()
        record(
            "404 不存在路由 → 40400 Not Found",
            r.status_code == 404 and body.get("code") == 40400
            and body.get("message") == "Not Found",
            f"status={r.status_code} body={body}",
        )

        # 1.3 带 token 空 body → 422 专用 handler
        token = await _login_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        r = await client.post("/api/v1/posts", json={}, headers=headers)
        body = r.json()
        record(
            "422 空 body → 42200 Field required",
            r.status_code == 422 and body.get("code") == 42200
            and body.get("message") == "Field required",
            f"status={r.status_code} body={body}",
        )

        # 1.4 无效 token 点赞 → 401 业务 dict detail 透传
        r = await client.post(
            "/api/v1/posts/1/like",
            headers={"Authorization": "Bearer invalid_token_xxx"},
        )
        body = r.json()
        record(
            "401 无效 token → 40101 无效的 Token（dict detail 透传）",
            r.status_code == 401 and body.get("code") == 40101
            and body.get("message") == "无效的 Token",
            f"status={r.status_code} body={body}",
        )


# ───────────────────────── 场景 2：Like 外键（#28）─────────────────────────
async def verify_like_fk():
    print("\n## 场景 2：Like 外键约束（#28）")
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text as sa_text
    from app.core.database import async_session_factory

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        post_id = await _create_post(client, headers)

        # 2.1 点赞不存在帖子 → 业务错误 40401（非 500）
        r = await client.post("/api/v1/posts/99999/like", headers=headers)
        body = r.json()
        record(
            "点赞不存在帖子 → 400/40401 帖子不存在（非 500）",
            r.status_code == 400 and body.get("code") == 40401
            and body.get("message") == "帖子不存在",
            f"status={r.status_code} body={body}",
        )

        # 2.2 点赞真实帖子 → 200
        r = await client.post(f"/api/v1/posts/{post_id}/like", headers=headers)
        body = r.json()
        record(
            "点赞真实帖子 → 200 liked=true likeCount=1",
            r.status_code == 200 and body.get("data", {}).get("liked") is True
            and body.get("data", {}).get("likeCount") == 1,
            f"status={r.status_code} body={body}",
        )

        # 2.3 取消点赞不存在帖子 → 200 幂等
        r = await client.delete("/api/v1/posts/99999/like", headers=headers)
        record(
            "取消点赞不存在帖子 → 200 幂等",
            r.status_code == 200,
            f"status={r.status_code}",
        )

        # 2.4 ORM 直插孤儿行 → IntegrityError（FK 在 ORM 连接生效）
        try:
            async with async_session_factory() as session:
                from app.models.like import Like
                session.add(Like(post_id=99999, user_id=99999))
                await session.commit()
            record("ORM 直插孤儿行 → IntegrityError", False, "竟然插入成功了（FK 未生效）")
        except Exception as exc:
            name = type(exc).__name__
            record(
                "ORM 直插孤儿行 → IntegrityError（FK 在 ORM 连接生效）",
                name == "IntegrityError",
                f"异常={name}",
            )

        # 2.5 服务层 delete_post 删帖 → likes 级联清除
        # 先确认点赞存在
        async with async_session_factory() as session:
            cnt = (await session.execute(
                sa_text("SELECT COUNT(*) FROM likes WHERE post_id = :pid"), {"pid": post_id}
            )).scalar()
        # 通过 API 删帖（作者本人）
        r = await client.delete(f"/api/v1/posts/{post_id}", headers=headers)
        async with async_session_factory() as session:
            cnt_after = (await session.execute(
                sa_text("SELECT COUNT(*) FROM likes WHERE post_id = :pid"), {"pid": post_id}
            )).scalar()
        record(
            "服务层删帖 → likes 自动清除（CASCADE 生效）",
            r.status_code == 200 and cnt == 1 and cnt_after == 0,
            f"删帖前 likes={cnt} 删帖后 likes={cnt_after} status={r.status_code}",
        )


# ───────────────────────── 场景 3：迁移增量验证（SQLite）─────────────────────────
def _alembic_config(mig_db: Path):
    """构造指向临时 SQLite 库的 Alembic Config（env.py 无 .env 时用 ini 的 sqlalchemy.url）"""
    from alembic.config import Config
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{mig_db}")
    return cfg


def verify_migration():
    print("\n## 场景 3：迁移 28d5e00fd033 增量验证（SQLite）")
    from alembic import command

    mig_db = Path(_TMPDIR) / "migrate_lanyuan.db"

    def schema_fks(db_path) -> list:
        """返回 likes 表的外键列表 [(列, 引用表, on_delete)]"""
        conn = sqlite3.connect(db_path)
        try:
            fks = conn.execute("PRAGMA foreign_key_list(likes)").fetchall()
            return [(f[3], f[2], f[6]) for f in fks]
        finally:
            conn.close()

    def likes_rows(db_path):
        conn = sqlite3.connect(db_path)
        try:
            return conn.execute("SELECT id, post_id, user_id FROM likes ORDER BY id").fetchall()
        finally:
            conn.close()

    # 3.0 stamp 到既有最新版本 c8eba06d0ef1（增量起点，跳过 avatar MySQL 迁移）
    cfg = _alembic_config(mig_db)
    try:
        command.stamp(cfg, "c8eba06d0ef1")
    except Exception as exc:
        record("stamp 到 c8eba06d0ef1", False, f"{type(exc).__name__}: {exc}")
        return

    # 预置旧结构表（与 88faa96a7d35 initial_schema 一致，likes 无 FK）+ 数据：
    # 1 用户 + 1 帖子 + 1 合法 like + 1 孤儿 like
    conn = sqlite3.connect(mig_db)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER NOT NULL PRIMARY KEY,
            openid VARCHAR(64) NOT NULL,
            nickname VARCHAR(32) NOT NULL,
            avatar VARCHAR(256) NOT NULL DEFAULT ''
        );
        CREATE TABLE posts (
            id INTEGER NOT NULL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            images JSON NOT NULL DEFAULT '[]',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
        );
        CREATE TABLE likes (
            id INTEGER NOT NULL PRIMARY KEY,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            CONSTRAINT uq_post_user_like UNIQUE (post_id, user_id)
        );
        INSERT INTO users (id, openid, nickname) VALUES (1, 'u1', 'u1');
        INSERT INTO posts (id, user_id, content) VALUES (1, 1, 'p1');
        INSERT INTO likes (id, post_id, user_id) VALUES (1, 1, 1);        -- 合法
        INSERT INTO likes (id, post_id, user_id) VALUES (2, 99999, 99999); -- 孤儿
        """
    )
    conn.commit()
    conn.close()

    # 3.1 upgrade → 孤儿清理 + 双 FK
    try:
        command.upgrade(cfg, "28d5e00fd033")
    except Exception as exc:
        record("upgrade 到 28d5e00fd033", False, f"{type(exc).__name__}: {exc}")
        return
    fks = schema_fks(mig_db)
    rows = likes_rows(mig_db)
    fk_posts = any(f[0] == "post_id" and f[1] == "posts" and f[2] == "CASCADE" for f in fks)
    fk_users = any(f[0] == "user_id" and f[1] == "users" and f[2] == "CASCADE" for f in fks)
    record(
        "upgrade：孤儿清理（合法保留 1 / 孤儿清除 0）",
        len(rows) == 1 and rows[0][1] == 1 and rows[0][2] == 1,
        f"likes 剩余 {len(rows)} 行: {rows}",
    )
    record(
        "upgrade：双 FK 建立（posts/users + ON DELETE CASCADE）",
        fk_posts and fk_users,
        f"外键: {fks}",
    )

    # 3.2 downgrade → FK 对称回滚
    try:
        command.downgrade(cfg, "c8eba06d0ef1")
    except Exception as exc:
        record("downgrade 到 c8eba06d0ef1", False, f"{type(exc).__name__}: {exc}")
        return
    fks_after = schema_fks(mig_db)
    record(
        "downgrade：FK 对称回滚移除",
        len(fks_after) == 0,
        f"回滚后外键: {fks_after}",
    )


async def main():
    print("=" * 64)
    print("  PR #31 review 触发验证（#27 统一错误格式 + #28 Like 外键）")
    print(f"  临时 DB: {_DB_PATH}")
    print("=" * 64)

    await _setup_db()
    try:
        await verify_error_format()
        await _clear_db()
        await verify_like_fk()
    finally:
        from app.core.database import close_db
        await close_db()

    verify_migration()

    print("\n" + "=" * 64)
    print("  验证汇总")
    print("=" * 64)
    all_ok = True
    for label, ok, _ in RESULTS:
        if not ok:
            all_ok = False
        print(f"  {'✅' if ok else '❌'} {label}")
    print(f"\n  结果: {'全部通过 ✅' if all_ok else '存在失败 ❌'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""add foreign keys to likes

Revision ID: 28d5e00fd033
Revises: c8eba06d0ef1
Create Date: 2026-08-02 11:44:23.195347

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '28d5e00fd033'
down_revision: Union[str, Sequence[str], None] = 'c8eba06d0ef1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    # 清理存量孤儿数据，否则 MySQL 建 FK 约束会因违反行失败（issue #28 注意点 1）
    op.execute(
        "DELETE FROM likes WHERE post_id NOT IN (SELECT id FROM posts) "
        "OR user_id NOT IN (SELECT id FROM users)"
    )
    if bind.dialect.name == "sqlite":
        # SQLite 不支持 ALTER TABLE ADD CONSTRAINT，需 batch 重建表
        with op.batch_alter_table("likes") as batch_op:
            batch_op.create_foreign_key(
                "fk_likes_post_id", "posts", ["post_id"], ["id"], ondelete="CASCADE"
            )
            batch_op.create_foreign_key(
                "fk_likes_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE"
            )
    else:
        op.create_foreign_key(
            "fk_likes_post_id", "likes", "posts", ["post_id"], ["id"], ondelete="CASCADE"
        )
        op.create_foreign_key(
            "fk_likes_user_id", "likes", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("likes") as batch_op:
            batch_op.drop_constraint("fk_likes_post_id", type_="foreignkey")
            batch_op.drop_constraint("fk_likes_user_id", type_="foreignkey")
    else:
        op.drop_constraint("fk_likes_post_id", "likes", type_="foreignkey")
        op.drop_constraint("fk_likes_user_id", "likes", type_="foreignkey")

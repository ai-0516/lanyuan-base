"""add foreign key to user_memories

Revision ID: 4f8a2c9e1b3d
Revises: 3f9b1c2d4e5a
Create Date: 2026-08-03 15:10:00.000000

review #1/#15：与 #28 对齐，user_memories.user_id 加 FK → users.id
(ondelete CASCADE)；顺带删掉多余 index（FK 自带索引）。
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '4f8a2c9e1b3d'
down_revision: Union[str, Sequence[str], None] = '3f9b1c2d4e5a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    # 清理存量孤儿数据，否则建 FK 约束会因违反行失败（issue #28 注意点 1）
    op.execute(
        "DELETE FROM user_memories WHERE user_id NOT IN (SELECT id FROM users)"
    )
    if bind.dialect.name == "sqlite":
        # SQLite 不支持 ALTER TABLE ADD CONSTRAINT，需 batch 重建表
        with op.batch_alter_table("user_memories") as batch_op:
            batch_op.drop_index("ix_user_memories_user_id")
            batch_op.create_foreign_key(
                "fk_user_memories_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE"
            )
    else:
        op.drop_index("ix_user_memories_user_id", table_name="user_memories")
        op.create_foreign_key(
            "fk_user_memories_user_id", "user_memories", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("user_memories") as batch_op:
            batch_op.drop_constraint("fk_user_memories_user_id", type_="foreignkey")
            batch_op.create_index("ix_user_memories_user_id", ["user_id"], unique=False)
    else:
        op.drop_constraint("fk_user_memories_user_id", "user_memories", type_="foreignkey")
        op.create_index("ix_user_memories_user_id", "user_memories", ["user_id"], unique=False)

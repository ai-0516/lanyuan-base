"""add media moderation

Revision ID: 7a9c1d2e3f40
Revises: c2f7a9d4e5b6
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "7a9c1d2e3f40"
down_revision: Union[str, Sequence[str], None] = "c2f7a9d4e5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column("moderation_status", sa.String(length=16), nullable=False, server_default="approved"),
    )
    op.create_index("ix_posts_moderation_status", "posts", ["moderation_status"])
    op.create_table(
        "media_moderation_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("file_id", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trace_id"),
    )
    op.create_index("ix_media_moderation_tasks_post_id", "media_moderation_tasks", ["post_id"])
    op.create_index("ix_media_moderation_tasks_trace_id", "media_moderation_tasks", ["trace_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_media_moderation_tasks_trace_id", table_name="media_moderation_tasks")
    op.drop_index("ix_media_moderation_tasks_post_id", table_name="media_moderation_tasks")
    op.drop_table("media_moderation_tasks")
    op.drop_index("ix_posts_moderation_status", table_name="posts")
    op.drop_column("posts", "moderation_status")

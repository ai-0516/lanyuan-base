"""generalize media moderation tasks

Revision ID: c4f6a8b2d9e1
Revises: b6d2e4f8a1c3
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c4f6a8b2d9e1"
down_revision: Union[str, Sequence[str], None] = "b6d2e4f8a1c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POST = "post"
PARKING_RENTAL = "parking_rental"


def _create_generic_table() -> None:
    op.create_table(
        "media_moderation_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("file_id", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def _create_generic_indexes() -> None:
    op.create_index(
        "ix_media_moderation_tasks_resource",
        "media_moderation_tasks",
        ["resource_type", "resource_id"],
    )
    op.create_index(
        "ix_media_moderation_tasks_trace_id",
        "media_moderation_tasks",
        ["trace_id"],
        unique=True,
    )


def upgrade() -> None:
    connection = op.get_bind()
    duplicate_count = connection.execute(sa.text("""
        SELECT COUNT(*)
        FROM media_moderation_tasks AS post_task
        INNER JOIN parking_rental_media_moderation_tasks AS rental_task
          ON rental_task.trace_id = post_task.trace_id
    """)).scalar_one()
    if duplicate_count:
        raise RuntimeError("两类图片审核任务存在重复 trace_id，无法安全合并")

    op.rename_table("media_moderation_tasks", "post_media_moderation_tasks_legacy")
    op.rename_table(
        "parking_rental_media_moderation_tasks",
        "parking_rental_media_moderation_tasks_legacy",
    )
    _create_generic_table()
    connection.execute(sa.text("""
        INSERT INTO media_moderation_tasks
            (id, resource_type, resource_id, trace_id, file_id, status, created_at, updated_at)
        SELECT id, :resource_type, post_id, trace_id, file_id, status, created_at, updated_at
        FROM post_media_moderation_tasks_legacy
    """), {"resource_type": POST})
    connection.execute(sa.text("""
        INSERT INTO media_moderation_tasks
            (resource_type, resource_id, trace_id, file_id, status, created_at, updated_at)
        SELECT :resource_type, rental_id, trace_id, file_id, status, created_at, updated_at
        FROM parking_rental_media_moderation_tasks_legacy
    """), {"resource_type": PARKING_RENTAL})
    op.drop_table("parking_rental_media_moderation_tasks_legacy")
    op.drop_table("post_media_moderation_tasks_legacy")
    _create_generic_indexes()


def downgrade() -> None:
    op.rename_table("media_moderation_tasks", "media_moderation_tasks_generic_legacy")
    op.create_table(
        "media_moderation_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("file_id", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "parking_rental_media_moderation_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rental_id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("file_id", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["rental_id"], ["parking_rentals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    connection = op.get_bind()
    connection.execute(sa.text("""
        INSERT INTO media_moderation_tasks
            (id, post_id, trace_id, file_id, status, created_at, updated_at)
        SELECT id, resource_id, trace_id, file_id, status, created_at, updated_at
        FROM media_moderation_tasks_generic_legacy
        WHERE resource_type = :resource_type
    """), {"resource_type": POST})
    connection.execute(sa.text("""
        INSERT INTO parking_rental_media_moderation_tasks
            (id, rental_id, trace_id, file_id, status, created_at, updated_at)
        SELECT id, resource_id, trace_id, file_id, status, created_at, updated_at
        FROM media_moderation_tasks_generic_legacy
        WHERE resource_type = :resource_type
    """), {"resource_type": PARKING_RENTAL})
    op.drop_table("media_moderation_tasks_generic_legacy")
    op.create_index(
        "ix_media_moderation_tasks_post_id",
        "media_moderation_tasks",
        ["post_id"],
    )
    op.create_index(
        "ix_media_moderation_tasks_trace_id",
        "media_moderation_tasks",
        ["trace_id"],
        unique=True,
    )
    op.create_index(
        "ix_parking_rental_media_moderation_tasks_rental_id",
        "parking_rental_media_moderation_tasks",
        ["rental_id"],
    )
    op.create_index(
        "ix_parking_rental_media_moderation_tasks_trace_id",
        "parking_rental_media_moderation_tasks",
        ["trace_id"],
        unique=True,
    )

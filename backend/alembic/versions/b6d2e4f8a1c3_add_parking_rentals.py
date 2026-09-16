"""add parking rentals

Revision ID: b6d2e4f8a1c3
Revises: 7a9c1d2e3f40
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b6d2e4f8a1c3"
down_revision: Union[str, Sequence[str], None] = "7a9c1d2e3f40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "parking_rentals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("spot_id", sa.String(length=16), nullable=False),
        sa.Column("area", sa.String(length=8), nullable=False),
        sa.Column("nearby_building", sa.String(length=32), nullable=True),
        sa.Column("price_monthly", sa.Integer(), nullable=False),
        sa.Column("rental_term", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("contact", sa.String(length=128), nullable=False),
        sa.Column("images", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("moderation_status", sa.String(length=16), server_default="approved", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    indexed_columns = [
        "user_id", "spot_id", "area", "nearby_building", "price_monthly",
        "status", "moderation_status", "created_at",
    ]
    for column in indexed_columns:
        op.create_index(f"ix_parking_rentals_{column}", "parking_rentals", [column])
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


def downgrade() -> None:
    op.drop_table("parking_rental_media_moderation_tasks")
    op.drop_table("parking_rentals")

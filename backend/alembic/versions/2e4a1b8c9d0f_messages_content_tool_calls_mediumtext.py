"""messages content/tool_calls MEDIUMTEXT → Text（兼容 SQLite 测试）

Revision ID: 2e4a1b8c9d0f
Revises: 43a2b107dae2
Create Date: 2026-07-27 13:28:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "2e4a1b8c9d0f"
down_revision: Union[str, None] = "43a2b107dae2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "messages", "content",
        existing_type=sa.Text(),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.alter_column(
        "messages", "tool_calls",
        existing_type=sa.Text(),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "messages", "content",
        existing_type=sa.Text(),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.alter_column(
        "messages", "tool_calls",
        existing_type=sa.Text(),
        type_=sa.Text(),
        existing_nullable=True,
    )

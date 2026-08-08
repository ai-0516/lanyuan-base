"""add tool_name column to messages

Revision ID: 5a1b2c3d4e5f
Revises: 43a2b107dae2
Create Date: 2026-08-08 13:10:00.000000

review #53：tool 消息回填时直接存 tool_name，orm_to_canonical 无需
按 tool_call_id 反向匹配 assistant tool_calls（兼容旧数据：NULL 时兜底匹配）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a1b2c3d4e5f'
down_revision: Union[str, Sequence[str], None] = '4f8a2c9e1b3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("messages", sa.Column("tool_name", sa.String(length=100), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("messages", "tool_name")

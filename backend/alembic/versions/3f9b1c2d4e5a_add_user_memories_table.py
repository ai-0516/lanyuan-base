"""add user_memories table

Revision ID: 3f9b1c2d4e5a
Revises: 28d5e00fd033
Create Date: 2026-08-02 18:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f9b1c2d4e5a'
down_revision: Union[str, Sequence[str], None] = '28d5e00fd033'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('user_memories',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False, comment='用户 ID'),
    sa.Column('name', sa.String(length=100), nullable=False, comment='短标识（kebab-case）'),
    sa.Column('type', sa.String(length=20), nullable=False, comment='记忆类型: user / reference'),
    sa.Column('description', sa.String(length=255), nullable=False, comment='一行摘要（用于索引）'),
    sa.Column('body', sa.Text(), nullable=False, comment='完整内容（按需加载）'),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False, comment='创建时间'),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False, comment='更新时间'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_memories_user_id'), 'user_memories', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_user_memories_user_id'), table_name='user_memories')
    op.drop_table('user_memories')

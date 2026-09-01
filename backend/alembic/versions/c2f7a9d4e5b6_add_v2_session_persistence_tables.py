"""add v2 session persistence tables (sessions/events/persistence_state)

v2 M3 review 修复（PR #97 snxly 意见）：v2 会话三表由 backend/alembic 统一管理，
DSH 侧 mysql-persistence 插件不再自建表（删除 ensureSchema）。

表结构 = TECH_SPEC §8.2（原 dsh/mysql-persistence/src/schema.ts SCHEMA_DDL 同源，
改表两处必须同步——alembic 为生产真源，schema.ts DDL 仅测试自建用）。

Revision ID: c2f7a9d4e5b6
Revises: 5a1b2c3d4e5f
Create Date: 2026-09-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c2f7a9d4e5b6'
down_revision: Union[str, Sequence[str], None] = '5a1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """v2 会话三表（§8.2）。MySQL 用 InnoDB/utf8mb4（kwargs 方言专用，SQLite 忽略）；
    ignorable/singleton 用 SmallInteger 兼容两方言（MySQL 原 DDL 为 TINYINT，语义等价）。"""
    op.create_table(
        'sessions',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('version', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('cwd', sa.String(length=1024), nullable=True),
        sa.Column('parent_session', sa.String(length=64), nullable=True),
        sa.Column('seed_length', sa.BigInteger(), nullable=True),
        sa.Column('origin', sa.String(length=64), nullable=True),
        sa.Column('delegation_depth', sa.BigInteger(), nullable=True),
        sa.Column('agent_preset', sa.String(length=256), nullable=True),
        sa.Column('incarnation', sa.CHAR(length=36), nullable=False),
        sa.Column('revision', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.Column('owner_user_id', sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    # PR #97 dev-lead review：get_or_create_session_v2 按 (owner_user_id,
    # created_at DESC) 查「用户最近会话」——补索引避免每用户全表扫描
    op.create_index(
        'ix_sessions_owner_user_id_created_at',
        'sessions',
        ['owner_user_id', 'created_at'],
    )
    op.create_table(
        'events',
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('seq', sa.BigInteger(), nullable=False),
        sa.Column('type', sa.String(length=128), nullable=False),
        sa.Column('time', sa.BigInteger(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=True),
        sa.Column('source_event_seqs', sa.JSON(), nullable=True),
        sa.Column('surface_op', sa.String(length=64), nullable=True),
        sa.Column('ignorable', sa.SmallInteger(), nullable=True),
        sa.PrimaryKeyConstraint('session_id', 'seq'),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='CASCADE'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_table(
        'persistence_state',
        sa.Column('singleton', sa.SmallInteger(), nullable=False),
        sa.Column('store_id', sa.CHAR(length=36), nullable=False),
        sa.PrimaryKeyConstraint('singleton'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )


def downgrade() -> None:
    op.drop_index('ix_sessions_owner_user_id_created_at', table_name='sessions')
    op.drop_table('persistence_state')
    op.drop_table('events')
    op.drop_table('sessions')

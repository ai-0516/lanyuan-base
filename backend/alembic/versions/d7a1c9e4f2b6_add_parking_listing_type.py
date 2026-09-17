"""add parking listing type

Revision ID: d7a1c9e4f2b6
Revises: c4f6a8b2d9e1
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "d7a1c9e4f2b6"
down_revision: Union[str, Sequence[str], None] = "c4f6a8b2d9e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("parking_rentals") as batch:
        batch.add_column(sa.Column("listing_type", sa.String(16), server_default="offer", nullable=False))
        batch.alter_column("spot_id", existing_type=sa.String(16), nullable=True)
        batch.alter_column("price_monthly", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("rental_term", existing_type=sa.String(64), nullable=True)
        batch.create_index("ix_parking_rentals_listing_type", ["listing_type"])


def downgrade() -> None:
    count = op.get_bind().execute(sa.text(
        "SELECT COUNT(*) FROM parking_rentals "
        "WHERE listing_type = 'wanted' OR price_monthly IS NULL OR rental_term IS NULL"
    )).scalar_one()
    if count:
        raise RuntimeError("cannot downgrade while new-format parking listings exist")
    with op.batch_alter_table("parking_rentals") as batch:
        batch.drop_index("ix_parking_rentals_listing_type")
        batch.alter_column("spot_id", existing_type=sa.String(16), nullable=False)
        batch.alter_column("price_monthly", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("rental_term", existing_type=sa.String(64), nullable=False)
        batch.drop_column("listing_type")

"""dataset reference tables + descriptive transaction columns

Revision ID: e8f9a0b1c2d3
Revises: d4e5f6a7b8c9
Create Date: 2026-10-03 23:45:00.000000

Support for the synthetic dataset (data/dataset/):
- customers / merchants reference tables the customer-facing API resolves
  profiles and merchant names against,
- four descriptive (non-risk) columns on transactions filled by the
  dataset loader,
- blocked_reason widened to 96 (dataset sentences run to 68 chars).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8f9a0b1c2d3'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'customers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('customer_id', sa.String(length=64), nullable=False),
        sa.Column('full_name', sa.String(length=120), nullable=False),
        sa.Column('email', sa.String(length=160), nullable=False),
        sa.Column('phone', sa.String(length=40), nullable=False),
        sa.Column('country', sa.String(length=2), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('segment', sa.String(length=16), nullable=False),
        sa.Column('risk_profile', sa.String(length=16), nullable=False),
        sa.Column('archetype', sa.String(length=32), nullable=True),
    )
    op.create_index('ix_customers_customer_id', 'customers', ['customer_id'], unique=True)

    op.create_table(
        'merchants',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('merchant_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('country', sa.String(length=2), nullable=False),
        sa.Column('risk_tier', sa.String(length=16), nullable=False),
    )
    op.create_index('ix_merchants_merchant_id', 'merchants', ['merchant_id'], unique=True)

    with op.batch_alter_table('transactions') as batch:
        batch.add_column(sa.Column('transaction_type', sa.String(length=32), nullable=True))
        batch.add_column(sa.Column('channel', sa.String(length=16), nullable=True))
        batch.add_column(sa.Column('direction', sa.String(length=8), nullable=True))
        batch.add_column(sa.Column('country', sa.String(length=2), nullable=True))

    with op.batch_alter_table('recovery_actions') as batch:
        batch.alter_column('blocked_reason', existing_type=sa.String(length=48), type_=sa.String(length=96))


def downgrade() -> None:
    with op.batch_alter_table('recovery_actions') as batch:
        batch.alter_column('blocked_reason', existing_type=sa.String(length=96), type_=sa.String(length=48))

    with op.batch_alter_table('transactions') as batch:
        batch.drop_column('country')
        batch.drop_column('direction')
        batch.drop_column('channel')
        batch.drop_column('transaction_type')

    op.drop_index('ix_merchants_merchant_id', table_name='merchants')
    op.drop_table('merchants')
    op.drop_index('ix_customers_customer_id', table_name='customers')
    op.drop_table('customers')

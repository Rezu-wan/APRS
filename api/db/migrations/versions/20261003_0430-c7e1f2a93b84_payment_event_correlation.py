"""payment_event correlation columns

Revision ID: c7e1f2a93b84
Revises: b4038c3dee26
Create Date: 2026-10-03 04:30:00.000000

Stage 11A: nullable correlation columns on payment_events.
correlation_id is backfilled from transaction_id in one UPDATE (existing
provider-observed events are correlated to their transaction); causation_id
stays NULL for them (provider-observed events have no internal cause).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e1f2a93b84'
down_revision: Union[str, None] = 'b4038c3dee26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'payment_events',
        sa.Column('correlation_id', sa.String(length=64), nullable=True)
    )
    op.add_column(
        'payment_events',
        sa.Column('causation_id', sa.String(length=64), nullable=True)
    )
    op.add_column(
        'payment_events',
        sa.Column('schema_version', sa.String(length=8), nullable=True)
    )
    op.execute(
        "UPDATE payment_events SET correlation_id = transaction_id "
        "WHERE correlation_id IS NULL"
    )


def downgrade() -> None:
    op.drop_column('payment_events', 'schema_version')
    op.drop_column('payment_events', 'causation_id')
    op.drop_column('payment_events', 'correlation_id')

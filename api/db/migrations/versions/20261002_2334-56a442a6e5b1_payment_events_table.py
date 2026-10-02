"""payment_events table

Revision ID: 56a442a6e5b1
Revises: 4957757f2953
Create Date: 2026-10-02 23:34:04.753882

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '56a442a6e5b1'
down_revision: Union[str, None] = '4957757f2953'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Stage 6 payment-DOMAIN evidence stream — append-only, replay-safe.
    # UNIQUE(provider_event_id) is the idempotency anchor (providers redeliver
    # events); UNIQUE(event_id) mirrors the sibling twin/recovery tables.
    op.create_table('payment_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('event_id', sa.String(length=36), nullable=False),
    sa.Column('transaction_id', sa.String(length=64), nullable=False),
    sa.Column('provider_event_id', sa.String(length=128), nullable=False),
    sa.Column('event_type', sa.String(length=48), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('event_timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reference_id', sa.String(length=128), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('metadata', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['transaction_id'], ['transactions.transaction_id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('payment_events', schema=None) as batch_op:
        # UNIQUE(provider_event_id) is the idempotency anchor: providers
        # redeliver events, so replays must be safe. No index on event_type:
        # 14 low-cardinality values, the reconstruction engine always reads
        # full streams per transaction, so it cannot pay for itself.
        batch_op.create_index(batch_op.f('ix_payment_events_event_timestamp'), ['event_timestamp'], unique=False)
        batch_op.create_index(batch_op.f('ix_payment_events_event_id'), ['event_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_payment_events_provider_event_id'), ['provider_event_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_payment_events_transaction_id'), ['transaction_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('payment_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payment_events_transaction_id'))
        batch_op.drop_index(batch_op.f('ix_payment_events_provider_event_id'))
        batch_op.drop_index(batch_op.f('ix_payment_events_event_timestamp'))

    op.drop_table('payment_events')

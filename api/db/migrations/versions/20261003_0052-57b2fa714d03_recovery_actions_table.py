"""recovery_actions table

Revision ID: 57b2fa714d03
Revises: dfd33619148d
Create Date: 2026-10-03 00:52:43.061930

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '57b2fa714d03'
down_revision: Union[str, None] = 'dfd33619148d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Stage 8 autonomous recovery action — SANDBOX execution record. The
    # UNIQUE idempotency_key is the crash-safety anchor: sha256 over
    # transaction_id + action + policy_version + risk-evidence fingerprint,
    # so a retry after a crash between provider call and DB commit replays
    # instead of double-releasing. UNIQUE(recovery_id) mirrors the sibling
    # twin/recovery tables.
    op.create_table('recovery_actions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('recovery_id', sa.String(length=36), nullable=False),
    sa.Column('transaction_id', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=24), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=False),
    sa.Column('attempt_count', sa.Integer(), nullable=False),
    sa.Column('requested_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('released_amount', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('policy_version', sa.String(length=24), nullable=False),
    sa.Column('executor_version', sa.String(length=24), nullable=True),
    sa.Column('verifier_version', sa.String(length=24), nullable=True),
    sa.Column('risk_assessment_id', sa.String(length=36), nullable=True),
    sa.Column('decision_reason', sa.Text(), nullable=False),
    sa.Column('blocked_reason', sa.String(length=48), nullable=True),
    sa.Column('failure_reason', sa.Text(), nullable=True),
    sa.Column('provider', sa.String(length=16), nullable=True),
    sa.Column('provider_reference', sa.String(length=64), nullable=True),
    sa.Column('provider_result', sa.JSON(), nullable=True),
    sa.Column('verification_result', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['transaction_id'], ['transactions.transaction_id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('recovery_actions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_recovery_actions_recovery_id'), ['recovery_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_recovery_actions_transaction_id'), ['transaction_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_recovery_actions_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_recovery_actions_idempotency_key'), ['idempotency_key'], unique=True)
        batch_op.create_index(batch_op.f('ix_recovery_actions_risk_assessment_id'), ['risk_assessment_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_recovery_actions_created_at'), ['created_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('recovery_actions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_recovery_actions_created_at'))
        batch_op.drop_index(batch_op.f('ix_recovery_actions_risk_assessment_id'))
        batch_op.drop_index(batch_op.f('ix_recovery_actions_idempotency_key'))
        batch_op.drop_index(batch_op.f('ix_recovery_actions_status'))
        batch_op.drop_index(batch_op.f('ix_recovery_actions_transaction_id'))
        batch_op.drop_index(batch_op.f('ix_recovery_actions_recovery_id'))

    op.drop_table('recovery_actions')

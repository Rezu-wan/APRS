"""risk_assessments table

Revision ID: dfd33619148d
Revises: 56a442a6e5b1
Create Date: 2026-10-03 00:20:23.808450

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dfd33619148d'
down_revision: Union[str, None] = '56a442a6e5b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Stage 7 hybrid risk assessment — decision EVIDENCE for Stage 8, never
    # an action. Multiple rows per transaction (history); the latest row
    # matching the current evidence fingerprint is reused. UNIQUE(assessment_id)
    # mirrors the sibling twin/recovery tables; the fingerprint index is the
    # idempotency lookup path.
    op.create_table('risk_assessments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('assessment_id', sa.String(length=36), nullable=False),
    sa.Column('transaction_id', sa.String(length=64), nullable=False),
    sa.Column('evidence_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('anomaly_type', sa.String(length=32), nullable=False),
    sa.Column('risk_level', sa.String(length=16), nullable=False),
    sa.Column('risk_score', sa.Float(), nullable=False),
    sa.Column('ml_anomaly_score', sa.Float(), nullable=True),
    sa.Column('deterministic_risk_score', sa.Float(), nullable=False),
    sa.Column('recovery_candidate', sa.Boolean(), nullable=False),
    sa.Column('recovery_block_reason', sa.Text(), nullable=True),
    sa.Column('reconstruction_root_cause', sa.String(length=64), nullable=True),
    sa.Column('reconstruction_confidence', sa.Float(), nullable=True),
    sa.Column('customer_reported_failure', sa.Boolean(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.Column('triggered_rules', sa.JSON(), nullable=False),
    sa.Column('model_version', sa.String(length=32), nullable=False),
    sa.Column('rule_version', sa.String(length=8), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['transaction_id'], ['transactions.transaction_id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('risk_assessments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_risk_assessments_assessment_id'), ['assessment_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_risk_assessments_anomaly_type'), ['anomaly_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_risk_assessments_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_risk_assessments_evidence_fingerprint'), ['evidence_fingerprint'], unique=False)
        batch_op.create_index(batch_op.f('ix_risk_assessments_risk_level'), ['risk_level'], unique=False)
        batch_op.create_index(batch_op.f('ix_risk_assessments_transaction_id'), ['transaction_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('risk_assessments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_risk_assessments_transaction_id'))
        batch_op.drop_index(batch_op.f('ix_risk_assessments_risk_level'))
        batch_op.drop_index(batch_op.f('ix_risk_assessments_evidence_fingerprint'))
        batch_op.drop_index(batch_op.f('ix_risk_assessments_created_at'))
        batch_op.drop_index(batch_op.f('ix_risk_assessments_anomaly_type'))
        batch_op.drop_index(batch_op.f('ix_risk_assessments_assessment_id'))

    op.drop_table('risk_assessments')

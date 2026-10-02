"""security audit + sandbox ledger persistence

Revision ID: b4038c3dee26
Revises: 57b2fa714d03
Create Date: 2026-10-03 01:55:16.772039

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4038c3dee26'
down_revision: Union[str, None] = '57b2fa714d03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Stage 9 security audit trail — WHO did WHAT to WHICH resource. No
    # foreign keys on purpose: audit rows must survive even if the audited
    # resource is later removed, and actor_id is a key NAME, never a secret.
    op.create_table('security_audit',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('audit_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor_type', sa.String(length=16), nullable=False),
    sa.Column('actor_id', sa.String(length=48), nullable=False),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('resource_type', sa.String(length=32), nullable=True),
    sa.Column('resource_id', sa.String(length=128), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('result', sa.String(length=12), nullable=False),
    sa.Column('reason', sa.String(length=255), nullable=True),
    sa.Column('metadata', sa.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('security_audit', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_security_audit_audit_id'), ['audit_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_security_audit_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_security_audit_action'), ['action'], unique=False)
        batch_op.create_index(batch_op.f('ix_security_audit_request_id'), ['request_id'], unique=False)

    # Write-through persistence for the SIMULATED sandbox ledger. The
    # in-memory ledger stays the live state; this table only survives
    # process restarts. Primary key on transaction_id — one entry per tx.
    op.create_table('sandbox_ledger_entries',
    sa.Column('transaction_id', sa.String(length=64), nullable=False),
    sa.Column('held_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('released_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('provider_reference', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('transaction_id')
    )


def downgrade() -> None:
    op.drop_table('sandbox_ledger_entries')

    with op.batch_alter_table('security_audit', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_security_audit_request_id'))
        batch_op.drop_index(batch_op.f('ix_security_audit_action'))
        batch_op.drop_index(batch_op.f('ix_security_audit_created_at'))
        batch_op.drop_index(batch_op.f('ix_security_audit_audit_id'))

    op.drop_table('security_audit')

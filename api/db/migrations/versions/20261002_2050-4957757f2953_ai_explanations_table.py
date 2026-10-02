"""ai_explanations table

Revision ID: 4957757f2953
Revises: f67049448be8
Create Date: 2026-10-02 20:50:51.406529

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4957757f2953'
down_revision: Union[str, None] = 'f67049448be8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Derived, NON-AUTHORITATIVE GenAI explanation cache — the only table the
    # explanation layer writes. Never part of the decision path.
    op.create_table('ai_explanations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('explanation_id', sa.String(length=36), nullable=False),
    sa.Column('transaction_id', sa.String(length=64), nullable=False),
    sa.Column('language', sa.String(length=2), nullable=False),
    sa.Column('audience', sa.String(length=16), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=False),
    sa.Column('prompt_version', sa.String(length=8), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('is_fallback', sa.Boolean(), nullable=False),
    sa.Column('context_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['transaction_id'], ['transactions.transaction_id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ai_explanations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ai_explanations_context_fingerprint'), ['context_fingerprint'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_explanations_explanation_id'), ['explanation_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_ai_explanations_transaction_id'), ['transaction_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('ai_explanations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_explanations_transaction_id'))
        batch_op.drop_index(batch_op.f('ix_ai_explanations_explanation_id'))
        batch_op.drop_index(batch_op.f('ix_ai_explanations_context_fingerprint'))

    op.drop_table('ai_explanations')

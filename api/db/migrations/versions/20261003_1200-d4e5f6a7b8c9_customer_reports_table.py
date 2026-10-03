"""customer_reports table

Revision ID: d4e5f6a7b8c9
Revises: c7e1f2a93b84
Create Date: 2026-10-03 12:00:00.000000

Customer problem reports — decision EVIDENCE only. One report per
(transaction, customer); re-filing replays the stored row at the service
layer (already_reported=true), the UNIQUE constraint is the anchor.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c7e1f2a93b84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'customer_reports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('report_id', sa.String(length=36), nullable=False),
        sa.Column('transaction_id', sa.String(length=64), nullable=False),
        sa.Column('customer_id', sa.String(length=64), nullable=False),
        sa.Column('problem_type', sa.String(length=32), nullable=False),
        sa.Column('stage', sa.String(length=32), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.transaction_id']),
        sa.UniqueConstraint('transaction_id', 'customer_id', name='uq_customer_report'),
    )
    op.create_index('ix_customer_reports_report_id', 'customer_reports', ['report_id'])
    op.create_index('ix_customer_reports_transaction_id', 'customer_reports', ['transaction_id'])
    op.create_index('ix_customer_reports_customer_id', 'customer_reports', ['customer_id'])


def downgrade() -> None:
    op.drop_index('ix_customer_reports_customer_id', table_name='customer_reports')
    op.drop_index('ix_customer_reports_transaction_id', table_name='customer_reports')
    op.drop_index('ix_customer_reports_report_id', table_name='customer_reports')
    op.drop_table('customer_reports')

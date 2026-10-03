"""dataset leftovers after the support-workspace merge

Revision ID: e8f9a0b1c2d3
Revises: b7e4d09a51c2
Create Date: 2026-10-03 23:45:00.000000

Originally this revision created the customers/merchants reference tables
and added the four descriptive transaction columns. The support-workspace
migration (b7e4d09a51c2, merged from the db branch) already creates both
tables — with a superset of these columns — and adds those same columns
along with account_id/device_id/counterparty, so those DDL moves live
there now and this revision only carries what is still uniquely needed:
- blocked_reason widened to 96 (dataset sentences run to 68 chars).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8f9a0b1c2d3'
down_revision: Union[str, None] = 'b7e4d09a51c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('recovery_actions') as batch:
        batch.alter_column('blocked_reason', existing_type=sa.String(length=48), type_=sa.String(length=96))


def downgrade() -> None:
    with op.batch_alter_table('recovery_actions') as batch:
        batch.alter_column('blocked_reason', existing_type=sa.String(length=96), type_=sa.String(length=48))

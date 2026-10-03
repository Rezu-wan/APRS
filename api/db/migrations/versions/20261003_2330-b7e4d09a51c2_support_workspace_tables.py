"""support workspace tables

Revision ID: b7e4d09a51c2
Revises: c7e1f2a93b84
Create Date: 2026-10-03 23:30:00.000000

Support workspace (customer-care):

* customers / accounts / devices / merchants / customer_behavior_signals —
  registries loaded from the db-branch dataset (data/dataset/*.csv) by
  scripts/load_dataset_db.py. 100% synthetic display/investigation data;
  never decision inputs.
* support_cases — the support-team ticket queue (OPEN → … → CLOSED), tied
  to one transaction each, with an append-only JSON notes thread.
* transactions — nullable dataset-enrichment columns (channel/country/
  device/account/direction/type/counterparty). Null for transactions
  created through the ingest API; populated for dataset-loaded rows.
  Never part of the decision path.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
# (re-parented onto customer_reports during the support/db/customer merge —
# it originally branched off c7e1f2a93b84, which left two alembic heads)
revision: str = 'b7e4d09a51c2'
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
        sa.Column('phone', sa.String(length=32), nullable=True),
        sa.Column('country', sa.String(length=2), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('segment', sa.String(length=16), nullable=True),
        sa.Column('risk_profile', sa.String(length=16), nullable=True),
        sa.Column('archetype', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_customers_customer_id', 'customers', ['customer_id'], unique=True)
    op.create_index('ix_customers_email', 'customers', ['email'])
    op.create_index('ix_customers_status', 'customers', ['status'])

    op.create_table(
        'accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.String(length=64), nullable=False),
        sa.Column('customer_id', sa.String(length=64), nullable=False),
        sa.Column('account_type', sa.String(length=32), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('balance', sa.Numeric(14, 2), nullable=False),
        sa.Column('opening_balance', sa.Numeric(14, 2), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('is_primary', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.customer_id']),
    )
    op.create_index('ix_accounts_account_id', 'accounts', ['account_id'], unique=True)
    op.create_index('ix_accounts_customer_id', 'accounts', ['customer_id'])

    op.create_table(
        'devices',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('device_id', sa.String(length=64), nullable=False),
        sa.Column('os', sa.String(length=32), nullable=True),
        sa.Column('model', sa.String(length=64), nullable=True),
        sa.Column('trusted', sa.Boolean(), nullable=False),
        sa.Column('first_seen', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_devices_device_id', 'devices', ['device_id'], unique=True)

    op.create_table(
        'merchants',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('merchant_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=True),
        sa.Column('country', sa.String(length=2), nullable=True),
        sa.Column('risk_tier', sa.String(length=16), nullable=True),
        sa.Column('traffic_weight', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_merchants_merchant_id', 'merchants', ['merchant_id'], unique=True)

    op.create_table(
        'customer_behavior_signals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('behavior_id', sa.String(length=64), nullable=False),
        sa.Column('customer_id', sa.String(length=64), nullable=False),
        sa.Column('signal_name', sa.String(length=48), nullable=False),
        sa.Column('signal_value', sa.Float(), nullable=True),
        sa.Column('unit', sa.String(length=24), nullable=True),
        sa.Column('signal_level', sa.String(length=8), nullable=False),
        sa.Column('baseline_source', sa.String(length=32), nullable=True),
        sa.Column('window', sa.String(length=32), nullable=True),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.customer_id']),
    )
    op.create_index('ix_customer_behavior_signals_behavior_id', 'customer_behavior_signals', ['behavior_id'], unique=True)
    op.create_index('ix_customer_behavior_signals_customer_id', 'customer_behavior_signals', ['customer_id'])
    op.create_index('ix_customer_behavior_signals_signal_name', 'customer_behavior_signals', ['signal_name'])

    op.create_table(
        'support_cases',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('case_id', sa.String(length=36), nullable=False),
        sa.Column('transaction_id', sa.String(length=64), nullable=False),
        sa.Column('customer_id', sa.String(length=64), nullable=False),
        sa.Column('subject', sa.String(length=140), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('priority', sa.String(length=8), nullable=False),
        sa.Column('created_by', sa.String(length=64), nullable=False),
        sa.Column('assignee', sa.String(length=64), nullable=True),
        sa.Column('notes', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.transaction_id']),
    )
    op.create_index('ix_support_cases_case_id', 'support_cases', ['case_id'], unique=True)
    op.create_index('ix_support_cases_transaction_id', 'support_cases', ['transaction_id'])
    op.create_index('ix_support_cases_customer_id', 'support_cases', ['customer_id'])
    op.create_index('ix_support_cases_status', 'support_cases', ['status'])
    op.create_index('ix_support_cases_priority', 'support_cases', ['priority'])
    op.create_index('ix_support_cases_created_at', 'support_cases', ['created_at'])

    # dataset enrichment on transactions (nullable — ingest API never sets them)
    op.add_column('transactions', sa.Column('account_id', sa.String(length=64), nullable=True))
    op.add_column('transactions', sa.Column('device_id', sa.String(length=64), nullable=True))
    op.add_column('transactions', sa.Column('counterparty', sa.String(length=64), nullable=True))
    op.add_column('transactions', sa.Column('direction', sa.String(length=8), nullable=True))
    op.add_column('transactions', sa.Column('transaction_type', sa.String(length=24), nullable=True))
    op.add_column('transactions', sa.Column('channel', sa.String(length=24), nullable=True))
    op.add_column('transactions', sa.Column('country', sa.String(length=2), nullable=True))
    op.create_index('ix_transactions_account_id', 'transactions', ['account_id'])
    op.create_index('ix_transactions_device_id', 'transactions', ['device_id'])


def downgrade() -> None:
    op.drop_index('ix_transactions_device_id', table_name='transactions')
    op.drop_index('ix_transactions_account_id', table_name='transactions')
    op.drop_column('transactions', 'country')
    op.drop_column('transactions', 'channel')
    op.drop_column('transactions', 'transaction_type')
    op.drop_column('transactions', 'direction')
    op.drop_column('transactions', 'counterparty')
    op.drop_column('transactions', 'device_id')
    op.drop_column('transactions', 'account_id')
    op.drop_index('ix_support_cases_created_at', table_name='support_cases')
    op.drop_index('ix_support_cases_priority', table_name='support_cases')
    op.drop_index('ix_support_cases_status', table_name='support_cases')
    op.drop_index('ix_support_cases_customer_id', table_name='support_cases')
    op.drop_index('ix_support_cases_transaction_id', table_name='support_cases')
    op.drop_index('ix_support_cases_case_id', table_name='support_cases')
    op.drop_table('support_cases')
    op.drop_index('ix_customer_behavior_signals_signal_name', table_name='customer_behavior_signals')
    op.drop_index('ix_customer_behavior_signals_customer_id', table_name='customer_behavior_signals')
    op.drop_index('ix_customer_behavior_signals_behavior_id', table_name='customer_behavior_signals')
    op.drop_table('customer_behavior_signals')
    op.drop_index('ix_merchants_merchant_id', table_name='merchants')
    op.drop_table('merchants')
    op.drop_index('ix_devices_device_id', table_name='devices')
    op.drop_table('devices')
    op.drop_index('ix_accounts_customer_id', table_name='accounts')
    op.drop_index('ix_accounts_account_id', table_name='accounts')
    op.drop_table('accounts')
    op.drop_index('ix_customers_status', table_name='customers')
    op.drop_index('ix_customers_email', table_name='customers')
    op.drop_index('ix_customers_customer_id', table_name='customers')
    op.drop_table('customers')

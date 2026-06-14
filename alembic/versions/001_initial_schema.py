"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-06-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("api_key_hash", sa.String(128), nullable=True),
        sa.Column("webhook_secret", sa.String(64), nullable=True),
        sa.Column("webhook_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("default_mode", sa.String(16), server_default="paper", nullable=False),
        sa.Column("max_contracts", sa.Integer(), server_default="1", nullable=False),
        sa.Column("allowed_tickers", sa.Text(), nullable=True),
        sa.Column("live_trading_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), unique=True, nullable=False),
        sa.Column("lemon_squeezy_customer_id", sa.String(64), nullable=True),
        sa.Column("lemon_squeezy_subscription_id", sa.String(64), nullable=True),
        sa.Column("variant_id", sa.String(64), nullable=True),
        sa.Column("plan_name", sa.String(64), server_default="free", nullable=False),
        sa.Column("status", sa.String(32), server_default="none", nullable=False),
        sa.Column("renews_at", sa.DateTime(), nullable=True),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_subscriptions_lemon_squeezy_subscription_id", "subscriptions", ["lemon_squeezy_subscription_id"])

    op.create_table(
        "broker_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("broker", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), server_default="disconnected", nullable=False),
        sa.Column("account_id", sa.String(64), nullable=True),
        sa.Column("encrypted_credentials", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_broker_connections_user_id", "broker_connections", ["user_id"])

    op.create_table(
        "inbound_alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("raw_payload", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("subscription_active", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("processed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("skip_reason", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_inbound_alerts_user_id", "inbound_alerts", ["user_id"])
    op.create_index("ix_inbound_alerts_idempotency_key", "inbound_alerts", ["idempotency_key"])

    op.create_table(
        "trade_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("alert_id", sa.String(36), sa.ForeignKey("inbound_alerts.id"), nullable=True),
        sa.Column("broker", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("underlying", sa.String(16), nullable=False),
        sa.Column("option_type", sa.String(8), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("expiration", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("contract_symbol", sa.String(32), nullable=True),
        sa.Column("fill_price", sa.Float(), nullable=True),
        sa.Column("pnl", sa.Float(), nullable=True),
        sa.Column("intent_json", sa.Text(), nullable=True),
        sa.Column("broker_response_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_trade_executions_user_id", "trade_executions", ["user_id"])


def downgrade() -> None:
    op.drop_table("trade_executions")
    op.drop_table("inbound_alerts")
    op.drop_table("broker_connections")
    op.drop_table("subscriptions")
    op.drop_table("users")

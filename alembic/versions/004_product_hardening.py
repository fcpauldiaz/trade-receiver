"""product hardening fields and constraints

Revision ID: 004
Revises: 003
Create Date: 2026-06-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("default_broker", sa.String(32), nullable=True))
    op.add_column("trade_executions", sa.Column("broker_order_id", sa.String(64), nullable=True))
    op.create_unique_constraint("uq_broker_connections_user_broker", "broker_connections", ["user_id", "broker"])
    op.create_unique_constraint("uq_inbound_alerts_user_idempotency", "inbound_alerts", ["user_id", "idempotency_key"])


def downgrade() -> None:
    op.drop_constraint("uq_inbound_alerts_user_idempotency", "inbound_alerts", type_="unique")
    op.drop_constraint("uq_broker_connections_user_broker", "broker_connections", type_="unique")
    op.drop_column("trade_executions", "broker_order_id")
    op.drop_column("users", "default_broker")

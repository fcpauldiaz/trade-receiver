"""product hardening fields and constraints

Revision ID: 004
Revises: 003
Create Date: 2026-06-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns(table)}


def _index_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {index["name"] for index in inspect(bind).get_indexes(table)}


def upgrade() -> None:
    if "default_broker" not in _column_names("users"):
        op.add_column("users", sa.Column("default_broker", sa.String(32), nullable=True))
    if "broker_order_id" not in _column_names("trade_executions"):
        op.add_column("trade_executions", sa.Column("broker_order_id", sa.String(64), nullable=True))

    # SQLite/Turso do not support ALTER TABLE ADD CONSTRAINT; use unique indexes instead.
    if "uq_broker_connections_user_broker" not in _index_names("broker_connections"):
        op.create_index(
            "uq_broker_connections_user_broker",
            "broker_connections",
            ["user_id", "broker"],
            unique=True,
        )
    if "uq_inbound_alerts_user_idempotency" not in _index_names("inbound_alerts"):
        op.create_index(
            "uq_inbound_alerts_user_idempotency",
            "inbound_alerts",
            ["user_id", "idempotency_key"],
            unique=True,
        )


def downgrade() -> None:
    if "uq_inbound_alerts_user_idempotency" in _index_names("inbound_alerts"):
        op.drop_index("uq_inbound_alerts_user_idempotency", table_name="inbound_alerts")
    if "uq_broker_connections_user_broker" in _index_names("broker_connections"):
        op.drop_index("uq_broker_connections_user_broker", table_name="broker_connections")
    if "broker_order_id" in _column_names("trade_executions"):
        op.drop_column("trade_executions", "broker_order_id")
    if "default_broker" in _column_names("users"):
        op.drop_column("users", "default_broker")

"""webhook ingest audit events

Revision ID: 017
Revises: 016
Create Date: 2026-09-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    return set(inspect(bind).get_table_names())


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns(table)}


def upgrade() -> None:
    alert_columns = _column_names("inbound_alerts")
    if "inbound_webhook_id" not in alert_columns:
        op.add_column(
            "inbound_alerts",
            sa.Column("inbound_webhook_id", sa.String(length=36), nullable=True),
        )
        op.create_foreign_key(
            "fk_inbound_alerts_inbound_webhook_id",
            "inbound_alerts",
            "inbound_webhooks",
            ["inbound_webhook_id"],
            ["id"],
        )
        op.create_index(
            "ix_inbound_alerts_inbound_webhook_id",
            "inbound_alerts",
            ["inbound_webhook_id"],
        )

    if "webhook_ingest_events" not in _table_names():
        op.create_table(
            "webhook_ingest_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column(
                "inbound_webhook_id",
                sa.String(length=36),
                sa.ForeignKey("inbound_webhooks.id"),
                nullable=False,
            ),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("request_payload", sa.Text(), nullable=False),
            sa.Column(
                "alert_id",
                sa.String(length=36),
                sa.ForeignKey("inbound_alerts.id"),
                nullable=True,
            ),
            sa.Column("trade_id", sa.String(length=36), nullable=True),
            sa.Column("detail", sa.String(length=512), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(
            "ix_webhook_ingest_events_user_id",
            "webhook_ingest_events",
            ["user_id"],
        )
        op.create_index(
            "ix_webhook_ingest_events_inbound_webhook_id",
            "webhook_ingest_events",
            ["inbound_webhook_id"],
        )
        op.create_index(
            "ix_webhook_ingest_events_created_at",
            "webhook_ingest_events",
            ["created_at"],
        )


def downgrade() -> None:
    if "webhook_ingest_events" in _table_names():
        op.drop_index("ix_webhook_ingest_events_created_at", table_name="webhook_ingest_events")
        op.drop_index(
            "ix_webhook_ingest_events_inbound_webhook_id",
            table_name="webhook_ingest_events",
        )
        op.drop_index("ix_webhook_ingest_events_user_id", table_name="webhook_ingest_events")
        op.drop_table("webhook_ingest_events")

    alert_columns = _column_names("inbound_alerts")
    if "inbound_webhook_id" in alert_columns:
        op.drop_index("ix_inbound_alerts_inbound_webhook_id", table_name="inbound_alerts")
        op.drop_constraint("fk_inbound_alerts_inbound_webhook_id", "inbound_alerts", type_="foreignkey")
        op.drop_column("inbound_alerts", "inbound_webhook_id")

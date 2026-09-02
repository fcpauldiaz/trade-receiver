"""drop Creem customer/subscription ids from subscriptions

Revision ID: 018
Revises: 017
Create Date: 2026-09-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    rows = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _index_names(table: str) -> set[str]:
    bind = op.get_bind()
    rows = bind.execute(sa.text(f"PRAGMA index_list({table})")).fetchall()
    return {row[1] for row in rows}


def upgrade() -> None:
    indexes = _index_names("subscriptions")
    if "ix_subscriptions_creem_subscription_id" in indexes:
        op.drop_index("ix_subscriptions_creem_subscription_id", table_name="subscriptions")

    columns = _column_names("subscriptions")
    if "creem_subscription_id" in columns:
        op.drop_column("subscriptions", "creem_subscription_id")
    if "creem_customer_id" in columns:
        op.drop_column("subscriptions", "creem_customer_id")


def downgrade() -> None:
    columns = _column_names("subscriptions")
    if "creem_customer_id" not in columns:
        op.add_column("subscriptions", sa.Column("creem_customer_id", sa.String(64), nullable=True))
    if "creem_subscription_id" not in columns:
        op.add_column(
            "subscriptions",
            sa.Column("creem_subscription_id", sa.String(64), nullable=True),
        )

    indexes = _index_names("subscriptions")
    if "ix_subscriptions_creem_subscription_id" not in indexes:
        op.create_index(
            "ix_subscriptions_creem_subscription_id",
            "subscriptions",
            ["creem_subscription_id"],
        )

"""drop Lemon Squeezy columns from subscriptions

Revision ID: 012
Revises: 011
Create Date: 2026-08-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns(table)}


def _index_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {index["name"] for index in inspect(bind).get_indexes(table)}


def upgrade() -> None:
    indexes = _index_names("subscriptions")
    if "ix_subscriptions_lemon_squeezy_subscription_id" in indexes:
        op.drop_index(
            "ix_subscriptions_lemon_squeezy_subscription_id",
            table_name="subscriptions",
        )

    columns = _column_names("subscriptions")
    if "lemon_squeezy_subscription_id" in columns:
        op.drop_column("subscriptions", "lemon_squeezy_subscription_id")
    if "lemon_squeezy_customer_id" in columns:
        op.drop_column("subscriptions", "lemon_squeezy_customer_id")


def downgrade() -> None:
    columns = _column_names("subscriptions")
    if "lemon_squeezy_customer_id" not in columns:
        op.add_column(
            "subscriptions",
            sa.Column("lemon_squeezy_customer_id", sa.String(64), nullable=True),
        )
    if "lemon_squeezy_subscription_id" not in columns:
        op.add_column(
            "subscriptions",
            sa.Column("lemon_squeezy_subscription_id", sa.String(64), nullable=True),
        )

    indexes = _index_names("subscriptions")
    if "ix_subscriptions_lemon_squeezy_subscription_id" not in indexes:
        op.create_index(
            "ix_subscriptions_lemon_squeezy_subscription_id",
            "subscriptions",
            ["lemon_squeezy_subscription_id"],
        )

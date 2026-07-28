"""add broker_connections.environment for Tradier sandbox/live

Revision ID: 009
Revises: 008
Create Date: 2026-07-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns(table)}


def upgrade() -> None:
    columns = _column_names("broker_connections")
    if "environment" not in columns:
        op.add_column(
            "broker_connections",
            sa.Column("environment", sa.String(16), nullable=True),
        )


def downgrade() -> None:
    columns = _column_names("broker_connections")
    if "environment" in columns:
        op.drop_column("broker_connections", "environment")

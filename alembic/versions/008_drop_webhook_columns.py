"""drop legacy per-user webhook columns

Revision ID: 008
Revises: 007
Create Date: 2026-06-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns(table)}


def upgrade() -> None:
    columns = _column_names("users")
    if "webhook_secret" in columns:
        op.drop_column("users", "webhook_secret")
    if "webhook_enabled" in columns:
        op.drop_column("users", "webhook_enabled")


def downgrade() -> None:
    columns = _column_names("users")
    if "webhook_secret" not in columns:
        op.add_column("users", sa.Column("webhook_secret", sa.String(64), nullable=True))
    if "webhook_enabled" not in columns:
        op.add_column(
            "users",
            sa.Column("webhook_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        )

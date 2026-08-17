"""add users.trade_filter_prompt

Revision ID: 013
Revises: 012
Create Date: 2026-08-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns(table)}


def upgrade() -> None:
    columns = _column_names("users")
    if "trade_filter_prompt" not in columns:
        op.add_column("users", sa.Column("trade_filter_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    columns = _column_names("users")
    if "trade_filter_prompt" in columns:
        op.drop_column("users", "trade_filter_prompt")

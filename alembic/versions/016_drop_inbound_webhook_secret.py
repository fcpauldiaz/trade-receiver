"""drop inbound_webhooks secret_hash column

Revision ID: 016
Revises: 015
Create Date: 2026-09-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns(table)}


def upgrade() -> None:
    columns = _column_names("inbound_webhooks")
    if "secret_hash" in columns:
        op.drop_column("inbound_webhooks", "secret_hash")


def downgrade() -> None:
    columns = _column_names("inbound_webhooks")
    if "secret_hash" not in columns:
        op.add_column(
            "inbound_webhooks",
            sa.Column("secret_hash", sa.String(length=128), nullable=False, server_default=""),
        )

"""sizing and onboarding fields

Revision ID: 003
Revises: 002
Create Date: 2026-06-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("sizing_mode", sa.String(32), server_default="alert_inferred", nullable=False))
    op.add_column("users", sa.Column("fixed_contracts", sa.Integer(), server_default="1", nullable=False))
    op.add_column("users", sa.Column("risk_percent", sa.Float(), server_default="1.0", nullable=False))
    op.add_column("users", sa.Column("onboarding_completed", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column("users", "onboarding_completed")
    op.drop_column("users", "risk_percent")
    op.drop_column("users", "fixed_contracts")
    op.drop_column("users", "sizing_mode")

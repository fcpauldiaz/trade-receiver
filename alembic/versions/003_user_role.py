"""user role for admin access

Revision ID: 003
Revises: 002
Create Date: 2026-09-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_role_column() -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("users")}
    return "role" in columns


def upgrade() -> None:
    # 001 uses create_all against current models, so fresh installs may already
    # have users.role before this revision runs.
    if _has_role_column():
        return
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=16), server_default="user", nullable=False),
    )


def downgrade() -> None:
    if not _has_role_column():
        return
    op.drop_column("users", "role")

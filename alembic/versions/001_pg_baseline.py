"""postgresql baseline schema

Revision ID: 001
Revises:
Create Date: 2026-09-03

"""
from typing import Sequence, Union

from alembic import op

from app.database import Base
from app.models import tables as _tables  # noqa: F401

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())

"""ai evaluations log

Revision ID: 002
Revises: 001
Create Date: 2026-09-03

"""
from typing import Sequence, Union

from alembic import op

from app.models.tables import AiEvaluation

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    AiEvaluation.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    AiEvaluation.__table__.drop(bind=op.get_bind(), checkfirst=True)

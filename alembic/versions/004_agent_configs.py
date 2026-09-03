"""agent configs for admin model/prompt overrides

Revision ID: 004
Revises: 003
Create Date: 2026-09-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.models.tables import AgentConfig

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AGENT_KEYS = ("parse", "filter")


def upgrade() -> None:
    AgentConfig.__table__.create(bind=op.get_bind(), checkfirst=True)
    bind = op.get_bind()
    existing = {
        row[0]
        for row in bind.execute(sa.text("SELECT agent_key FROM agent_configs")).fetchall()
    }
    for key in _AGENT_KEYS:
        if key in existing:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO agent_configs (agent_key, model, system_prompt, user_prompt_template) "
                "VALUES (:key, NULL, NULL, NULL)"
            ),
            {"key": key},
        )


def downgrade() -> None:
    AgentConfig.__table__.drop(bind=op.get_bind(), checkfirst=True)

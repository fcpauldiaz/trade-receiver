"""unify Better Auth user into users

Revision ID: 010
Revises: 009
Create Date: 2026-08-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_TABLES = (
    "subscriptions",
    "broker_connections",
    "inbound_alerts",
    "trade_executions",
    "reviews",
    "session",
    "account",
)


def _table_names() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _index_names(table: str) -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}


def _repoint_foreign_keys(conn, old_id: str, new_id: str) -> None:
    tables = _table_names()
    for table in FK_TABLES:
        if table in tables:
            conn.execute(
                text(f"UPDATE {table} SET user_id = :new_id WHERE user_id = :old_id"),
                {"new_id": new_id, "old_id": old_id},
            )


def upgrade() -> None:
    conn = op.get_bind()
    tables = _table_names()
    if "users" not in tables:
        return

    columns = _column_names("users")
    if "email_verified" not in columns:
        op.add_column(
            "users",
            sa.Column("email_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        )
    if "image" not in columns:
        op.add_column("users", sa.Column("image", sa.Text(), nullable=True))
    if "updated_at" not in columns:
        op.add_column("users", sa.Column("updated_at", sa.DateTime(), nullable=True))

    columns = _column_names("users")
    if "better_auth_id" in columns:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        rows = conn.execute(
            text(
                "SELECT id, better_auth_id FROM users "
                "WHERE better_auth_id IS NOT NULL AND better_auth_id != id"
            )
        ).fetchall()
        for old_id, auth_id in rows:
            existing = conn.execute(
                text("SELECT id FROM users WHERE id = :auth_id"),
                {"auth_id": auth_id},
            ).fetchone()
            _repoint_foreign_keys(conn, old_id, auth_id)
            if existing:
                conn.execute(text("DELETE FROM users WHERE id = :old_id"), {"old_id": old_id})
            else:
                conn.execute(
                    text("UPDATE users SET id = :auth_id WHERE id = :old_id"),
                    {"auth_id": auth_id, "old_id": old_id},
                )
        if "ix_users_better_auth_id" in _index_names("users"):
            op.drop_index("ix_users_better_auth_id", table_name="users")
        op.drop_column("users", "better_auth_id")
        conn.execute(text("PRAGMA foreign_keys=ON"))

    tables = _table_names()
    if "user" in tables:
        conn.execute(
            text(
                "INSERT OR IGNORE INTO users (id, name, email, email_verified, image, created_at, updated_at) "
                "SELECT id, name, email, email_verified, image, created_at, updated_at FROM user"
            )
        )
        op.drop_table("user")


def downgrade() -> None:
    columns = _column_names("users")
    if "better_auth_id" not in columns:
        op.add_column("users", sa.Column("better_auth_id", sa.String(36), nullable=True))
        op.create_index("ix_users_better_auth_id", "users", ["better_auth_id"], unique=True)
    if "updated_at" in columns:
        op.drop_column("users", "updated_at")
    if "image" in columns:
        op.drop_column("users", "image")
    if "email_verified" in columns:
        op.drop_column("users", "email_verified")

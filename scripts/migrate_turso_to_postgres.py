#!/usr/bin/env python3
"""Copy a Turso/libSQL or SQLite Trade Desky database into PostgreSQL 18.

Usage:
  SOURCE_DATABASE_URL=libsql://... TURSO_AUTH_TOKEN=... \\
  DATABASE_URL=postgresql://trade:trade@localhost:5432/trade \\
  python scripts/migrate_turso_to_postgres.py

Do not copy while both databases are receiving writes. Freeze ingest first.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TABLE_ORDER = (
    "users",
    "session",
    "account",
    "verification",
    "jwks",
    "subscriptions",
    "broker_connections",
    "user_devices",
    "inbound_webhooks",
    "inbound_alerts",
    "processed_webhook_events",
    "trade_executions",
    "webhook_ingest_events",
    "reviews",
)

BOOLEAN_COLUMNS = {
    "users": {"email_verified", "live_trading_enabled", "onboarding_completed"},
    "inbound_webhooks": {"enabled"},
    "inbound_alerts": {"subscription_active", "processed"},
}

UNIX_MS_COLUMNS = {
    "users": {"created_at", "updated_at"},
    "session": {"expires_at", "created_at", "updated_at"},
    "account": {
        "access_token_expires_at",
        "refresh_token_expires_at",
        "created_at",
        "updated_at",
    },
    "verification": {"expires_at", "created_at", "updated_at"},
    "jwks": {"created_at", "expires_at"},
}

DATETIME_COLUMNS = {
    "reviews": {"created_at", "updated_at"},
    "subscriptions": {"renews_at", "ends_at", "updated_at"},
    "broker_connections": {"updated_at"},
    "user_devices": {"revoked_at", "last_seen_at", "created_at"},
    "inbound_webhooks": {"created_at", "updated_at"},
    "inbound_alerts": {"created_at"},
    "webhook_ingest_events": {"created_at"},
    "processed_webhook_events": {"created_at"},
    "trade_executions": {"created_at"},
}

MAX_LENGTH_COLUMNS = {
    "inbound_alerts": {"skip_reason": 255},
}

SKIP_SOURCE_TABLES = {"__drizzle_migrations", "alembic_version", "sqlite_sequence"}


def _source_url(raw: str) -> str:
    if raw.startswith("libsql://"):
        parsed = urlparse("sqlite+libsql://" + raw.removeprefix("libsql://"))
        if parsed.netloc and "secure" not in parsed.query:
            query = "secure=true" if not parsed.query else parsed.query + "&secure=true"
            return urlunparse(parsed._replace(query=query))
        return urlunparse(parsed)
    return raw


def _source_engine(url: str):
    resolved = _source_url(url)
    connect_args: dict = {}
    token = os.environ.get("TURSO_AUTH_TOKEN")
    if token and "libsql" in resolved:
        connect_args["auth_token"] = token
        try:
            import sqlalchemy_libsql  # noqa: F401
        except ImportError as exc:
            raise SystemExit(
                "Install migration extras: pip install -e '.[migrate]'"
            ) from exc
    return create_engine(resolved, connect_args=connect_args)


def _as_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "t", "yes"}
    return bool(value)


def _as_timestamptz(value: object, *, unix_ms: bool) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if unix_ms or value > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    raise TypeError(f"Cannot convert {type(value)!r} to timestamptz")


def _convert_row(table: str, row: dict) -> dict:
    converted = dict(row)
    for column in BOOLEAN_COLUMNS.get(table, ()):
        if column in converted:
            converted[column] = _as_bool(converted[column])
    for column in UNIX_MS_COLUMNS.get(table, ()):
        if column in converted:
            converted[column] = _as_timestamptz(converted[column], unix_ms=True)
    for column in DATETIME_COLUMNS.get(table, ()):
        if column in converted:
            converted[column] = _as_timestamptz(converted[column], unix_ms=False)
    for column, max_len in MAX_LENGTH_COLUMNS.get(table, {}).items():
        value = converted.get(column)
        if isinstance(value, str) and len(value) > max_len:
            converted[column] = value[:max_len]
    return converted


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _copy_table(source, dest, table: str) -> int:
    source_tables = set(inspect(source).get_table_names())
    if table not in source_tables:
        print(f"skip {table} (missing on source)")
        return 0
    dest_columns = {column["name"] for column in inspect(dest).get_columns(table)}
    source_columns = [column["name"] for column in inspect(source).get_columns(table)]
    columns = [name for name in source_columns if name in dest_columns]
    if not columns:
        print(f"skip {table} (no overlapping columns)")
        return 0
    col_sql = ", ".join(_quote(name) for name in columns)
    rows = source.execute(text(f"SELECT {col_sql} FROM {_quote(table)}")).mappings().all()
    if not rows:
        print(f"copied {table}: 0")
        return 0
    placeholders = ", ".join(f":{name}" for name in columns)
    insert_sql = text(
        f"INSERT INTO {_quote(table)} ({col_sql}) VALUES ({placeholders})"
    )
    payload = [_convert_row(table, dict(row)) for row in rows]
    dest.execute(insert_sql, payload)
    print(f"copied {table}: {len(payload)}")
    return len(payload)


def main() -> None:
    source_raw = os.environ.get("SOURCE_DATABASE_URL")
    if not source_raw:
        raise SystemExit("SOURCE_DATABASE_URL is required")
    dest_raw = os.environ.get("DATABASE_URL")
    if not dest_raw:
        raise SystemExit("DATABASE_URL is required")

    os.environ["DATABASE_URL"] = dest_raw
    from app.config import settings
    from app.database import normalized_database_url
    from app.models import tables as _tables  # noqa: F401
    from app.services.migrations import run_migrations

    settings.database_url = dest_raw
    run_migrations()

    source = _source_engine(source_raw)
    dest = create_engine(normalized_database_url(dest_raw), pool_pre_ping=True)
    counts: dict[str, tuple[int, int]] = {}
    try:
        with source.connect() as src_conn, dest.begin() as dest_conn:
            for table in TABLE_ORDER:
                copied = _copy_table(src_conn, dest_conn, table)
                src_count = 0
                if table in set(inspect(src_conn).get_table_names()):
                    src_count = src_conn.execute(
                        text(f"SELECT COUNT(*) FROM {_quote(table)}")
                    ).scalar_one()
                dest_count = dest_conn.execute(
                    text(f"SELECT COUNT(*) FROM {_quote(table)}")
                ).scalar_one()
                counts[table] = (src_count, dest_count)
                if table in SKIP_SOURCE_TABLES:
                    continue
                if src_count != dest_count and table in set(inspect(src_conn).get_table_names()):
                    raise SystemExit(
                        f"row count mismatch for {table}: source={src_count} dest={dest_count}"
                    )
                if copied != dest_count:
                    raise SystemExit(
                        f"insert count mismatch for {table}: copied={copied} dest={dest_count}"
                    )

            session_orphans = dest_conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM "session"
                    WHERE user_id NOT IN (SELECT id FROM users)
                    """
                )
            ).scalar_one()
            account_orphans = dest_conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM account
                    WHERE user_id NOT IN (SELECT id FROM users)
                    """
                )
            ).scalar_one()
            if session_orphans or account_orphans:
                raise SystemExit(
                    f"orphan FKs: session={session_orphans} account={account_orphans}"
                )
    finally:
        source.dispose()
        dest.dispose()

    print("verification:")
    for table, (src_count, dest_count) in counts.items():
        print(f"  {table}: source={src_count} dest={dest_count}")
    print("done")


if __name__ == "__main__":
    main()

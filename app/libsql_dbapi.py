"""libsql DBAPI shim that always uses positional ``?`` parameters.

Turso/libsql can Rust-panic on named placeholders (``:name`` / ``$name`` /
``@name``), especially with ``sync_url`` embedded replicas. SQLAlchemy's
sqlite dialect normally emits ``?``, but this wrapper converts any dict binds
defensively before they reach the native driver.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any

import libsql

_BINDING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (":", re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")),
    ("@", re.compile(r"@([a-zA-Z_][a-zA-Z0-9_]*)")),
    ("$", re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*)")),
)

paramstyle = libsql.paramstyle
sqlite_version_info = libsql.sqlite_version_info
Error = libsql.Error
LEGACY_TRANSACTION_CONTROL = libsql.LEGACY_TRANSACTION_CONTROL
Connection = libsql.Connection
Cursor = libsql.Cursor


def _binding_pattern(sql: str) -> re.Pattern[str] | None:
    for prefix, pattern in _BINDING_PATTERNS:
        if pattern.search(sql):
            return pattern
    return None


def named_to_positional(sql: str, parameters: Any) -> tuple[str, Any]:
    if parameters is None or not isinstance(parameters, dict):
        return sql, parameters

    pattern = _binding_pattern(sql)
    if pattern is None:
        return sql, parameters

    values: list[Any] = []

    def replacer(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in parameters:
            raise ValueError(f"SQL parameter {name!r} is not bound")
        values.append(parameters[name])
        return "?"

    return pattern.sub(replacer, sql), tuple(values)


class PositionalCursor:
    def __init__(self, inner: libsql.Cursor) -> None:
        self._inner = inner

    def execute(self, sql: str, parameters: Any = None) -> libsql.Cursor:
        sql, parameters = named_to_positional(sql, parameters)
        return self._inner.execute(sql, parameters)

    def executemany(self, sql: str, parameters: Iterable[Any] | None = None) -> libsql.Cursor:
        if parameters is None:
            return self._inner.executemany(sql, parameters)
        rows = list(parameters)
        if rows and isinstance(rows[0], dict):
            for row in rows:
                converted_sql, converted_params = named_to_positional(sql, row)
                self._inner.execute(converted_sql, converted_params)
            return self._inner
        return self._inner.executemany(sql, rows)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._inner)


class PositionalConnection:
    def __init__(self, inner: libsql.Connection) -> None:
        self._inner = inner

    def cursor(self) -> PositionalCursor:
        return PositionalCursor(self._inner.cursor())

    def execute(self, sql: str, parameters: Any = None) -> PositionalCursor:
        sql, parameters = named_to_positional(sql, parameters)
        return PositionalCursor(self._inner.execute(sql, parameters))

    def executemany(self, sql: str, parameters: Iterable[Any] | None = None) -> PositionalCursor:
        cursor = self.cursor()
        cursor.executemany(sql, parameters)
        return cursor

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def normalize_libsql_connect_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Map SQLAlchemy/pysqlite connect keys to the maintained libsql driver API."""
    if "check_same_thread" in kwargs:
        kwargs["_check_same_thread"] = kwargs.pop("check_same_thread")
    if "uri" in kwargs:
        kwargs["_uri"] = kwargs.pop("uri")
    return kwargs


def connect(*args: Any, **kwargs: Any) -> PositionalConnection:
    normalize_libsql_connect_kwargs(kwargs)
    return PositionalConnection(libsql.connect(*args, **kwargs))

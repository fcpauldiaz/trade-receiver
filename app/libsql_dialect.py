"""SQLAlchemy dialect for the production libsql Python driver.

sqlalchemy-libsql depends on libsql-experimental, which can Rust-panic under
concurrent FastAPI + embedded-replica use. This module mirrors the upstream
dialect but imports the maintained ``libsql`` package instead.
"""

from __future__ import annotations

import os
import urllib.parse

from sqlalchemy import util
from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite


def _build_connection_url(url, query, secure):
    query_str = urllib.parse.urlencode(sorted(query.items()))

    if not url.host:
        if query_str:
            return f"{url.database}?{query_str}"
        return url.database
    if secure:
        scheme = "https"
    else:
        scheme = "http"

    if url.username and url.password:
        netloc = f"{url.username}:{url.password}@{url.host}"
    elif url.username:
        netloc = f"{url.username}@{url.host}"
    else:
        netloc = url.host

    if url.port:
        netloc += f":{url.port}"

    return urllib.parse.urlunsplit(
        (
            scheme,
            netloc,
            url.database or "",
            query_str,
            "",
        )
    )


class SQLiteDialect_libsql(SQLiteDialect_pysqlite):
    driver = "libsql"
    supports_statement_cache = SQLiteDialect_pysqlite.supports_statement_cache

    @classmethod
    def import_dbapi(cls):
        import libsql

        return libsql

    def on_connect(self):
        import libsql

        sqlite3_connect = super().on_connect()

        def connect(conn):
            if isinstance(conn, libsql.Connection):
                return
            return sqlite3_connect(conn)

        return connect

    def create_connect_args(self, url):
        pysqlite_args = (
            ("uri", bool),
            ("timeout", float),
            ("isolation_level", str),
            ("detect_types", int),
            ("check_same_thread", bool),
            ("cached_statements", int),
            ("secure", bool),
        )
        opts = url.query
        libsql_opts: dict = {}
        for key, type_ in pysqlite_args:
            util.coerce_kw_type(opts, key, type_, dest=libsql_opts)

        if url.host:
            libsql_opts["uri"] = True

        if libsql_opts.get("uri", False):
            uri_opts = dict(opts)
            for key, type_ in pysqlite_args:
                uri_opts.pop(key, None)

            secure = libsql_opts.pop("secure", False)
            connect_url = _build_connection_url(url, uri_opts, secure)
        else:
            connect_url = url.database or ":memory:"
            if connect_url != ":memory:":
                connect_url = os.path.abspath(connect_url)

        if "check_same_thread" in libsql_opts:
            libsql_opts["_check_same_thread"] = libsql_opts.pop("check_same_thread")

        libsql_opts.setdefault("_check_same_thread", not self._is_url_file_db(url))

        return ([connect_url], libsql_opts)


dialect = SQLiteDialect_libsql

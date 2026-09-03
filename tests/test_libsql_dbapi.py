from sqlalchemy.engine.url import make_url

from app.libsql_dbapi import connect, named_to_positional, normalize_libsql_connect_kwargs
from app.libsql_dialect import SQLiteDialect_libsql


def test_named_to_positional_converts_colon_binds():
    sql = "SELECT * FROM users WHERE id = :id AND name = :name"
    params = {"id": "u1", "name": "alice"}
    converted_sql, converted_params = named_to_positional(sql, params)
    assert converted_sql == "SELECT * FROM users WHERE id = ? AND name = ?"
    assert converted_params == ("u1", "alice")


def test_named_to_positional_converts_dollar_binds():
    sql = "INSERT INTO t (a, b) VALUES ($a, $b)"
    params = {"a": 1, "b": 2}
    converted_sql, converted_params = named_to_positional(sql, params)
    assert converted_sql == "INSERT INTO t (a, b) VALUES (?, ?)"
    assert converted_params == (1, 2)


def test_named_to_positional_leaves_qmark_tuple_unchanged():
    sql = "SELECT * FROM users WHERE id = ?"
    params = ("u1",)
    converted_sql, converted_params = named_to_positional(sql, params)
    assert converted_sql == sql
    assert converted_params == params


def test_normalize_libsql_connect_kwargs_maps_driver_names():
    kwargs = {"uri": True, "check_same_thread": False, "auth_token": "secret"}
    normalize_libsql_connect_kwargs(kwargs)
    assert kwargs == {"_uri": True, "_check_same_thread": False, "auth_token": "secret"}


def test_remote_dialect_connect_args_use_libsql_driver_keywords():
    dialect = SQLiteDialect_libsql()
    args, opts = dialect.create_connect_args(
        make_url("sqlite+libsql://mydb-org.turso.io?secure=true")
    )
    assert args == ["https://mydb-org.turso.io"]
    assert "uri" not in opts
    assert "check_same_thread" not in opts
    assert opts["_uri"] is True
    assert opts["_check_same_thread"] is True


def test_remote_connect_accepts_dialect_kwargs():
    dialect = SQLiteDialect_libsql()
    args, opts = dialect.create_connect_args(
        make_url("sqlite+libsql://mydb-org.turso.io?secure=true")
    )
    connect(*args, **{**opts, "auth_token": "fake-token"})

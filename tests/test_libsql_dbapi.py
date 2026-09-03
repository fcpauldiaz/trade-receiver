from app.libsql_dbapi import named_to_positional


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

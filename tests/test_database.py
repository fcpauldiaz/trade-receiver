from app.database import _resolve_database_url


def test_resolve_local_libsql_url_to_sqlite():
    url = "sqlite+libsql:///./data/trade.db"
    assert _resolve_database_url(url) == "sqlite:///./data/trade.db"


def test_resolve_remote_libsql_url_unchanged():
    url = "sqlite+libsql://mydb.turso.io"
    assert _resolve_database_url(url) == url


def test_resolve_standard_sqlite_unchanged():
    url = "sqlite:///./data/trade.db"
    assert _resolve_database_url(url) == url

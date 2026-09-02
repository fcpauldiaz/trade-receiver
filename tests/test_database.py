from app.database import (
    _build_engine,
    database_connect_args,
    normalized_database_url,
)


def test_normalize_libsql_scheme_to_sqlalchemy():
    url = "libsql://mydb-org.turso.io"
    assert normalized_database_url(url) == "sqlite+libsql://mydb-org.turso.io?secure=true"


def test_normalize_remote_sqlite_libsql_adds_secure():
    url = "sqlite+libsql://mydb-org.turso.io"
    assert normalized_database_url(url) == "sqlite+libsql://mydb-org.turso.io?secure=true"


def test_normalize_local_libsql_file_unchanged():
    url = "sqlite+libsql:///./data/trade.db"
    assert normalized_database_url(url) == url


def test_normalize_standard_sqlite_unchanged():
    url = "sqlite:///./data/trade.db"
    assert normalized_database_url(url) == url


def test_connect_args_include_turso_token(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "turso_auth_token", "secret-token")
    url = normalized_database_url("libsql://mydb-org.turso.io")
    args = database_connect_args(url)
    assert args == {"auth_token": "secret-token"}


def test_build_engine_sqlite_file(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "sqlite:///./data/trade.db")
    engine = _build_engine()
    try:
        assert engine.dialect.name == "sqlite"
    finally:
        engine.dispose()


def test_build_engine_remote_libsql(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(
        settings, "database_url", "libsql://mydb-org.turso.io"
    )
    monkeypatch.setattr(settings, "turso_auth_token", "secret-token")
    engine = _build_engine()
    try:
        assert engine.dialect.name == "sqlite"
    finally:
        engine.dispose()


def test_build_engine_local_libsql_file(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(
        settings, "database_url", "sqlite+libsql:///./data/trade.db"
    )
    engine = _build_engine()
    try:
        assert engine.dialect.name == "sqlite"
    finally:
        engine.dispose()

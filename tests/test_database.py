from app.database import (
    _build_engine,
    database_connect_args,
    engine_database_url,
    normalized_database_url,
    uses_embedded_libsql_replica,
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
    assert args == {"auth_token": "secret-token", "_check_same_thread": False}


def test_engine_url_prefers_remote_when_sync_url_configured(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "sqlite+libsql:///./data/trade.db")
    monkeypatch.setattr(settings, "turso_sync_url", "libsql://mydb-org.turso.io")
    monkeypatch.setattr(settings, "turso_embedded_replica", False)
    assert engine_database_url() == "sqlite+libsql://mydb-org.turso.io?secure=true"
    assert not uses_embedded_libsql_replica()
    args = database_connect_args()
    assert "sync_url" not in args


def test_engine_url_keeps_embedded_replica_when_enabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "sqlite+libsql:///./data/trade.db")
    monkeypatch.setattr(settings, "turso_sync_url", "libsql://mydb-org.turso.io")
    monkeypatch.setattr(settings, "turso_embedded_replica", True)
    assert engine_database_url() == "sqlite+libsql:///./data/trade.db"
    assert uses_embedded_libsql_replica()
    args = database_connect_args()
    assert args["sync_url"] == "libsql://mydb-org.turso.io"


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
        assert engine.dialect.driver == "libsql"
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
        assert engine.dialect.driver == "libsql"
    finally:
        engine.dispose()


def test_database_runtime_summary_flags_sync_url_without_embedded_replica(monkeypatch):
    from app.config import settings
    from app.database import database_runtime_summary

    monkeypatch.setattr(settings, "database_url", "sqlite+libsql:///./data/trade.db")
    monkeypatch.setattr(settings, "turso_sync_url", "libsql://mydb-org.turso.io")
    monkeypatch.setattr(settings, "turso_embedded_replica", False)
    summary = database_runtime_summary()
    assert summary["sync_url_configured"] is True
    assert summary["embedded_replica"] is False
    assert summary["engine_url"] == "sqlite+libsql://mydb-org.turso.io?secure=true"


def test_build_engine_creates_independent_instances(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "sqlite:///./data/trade.db")
    first = _build_engine()
    second = _build_engine()
    try:
        assert first is not second
    finally:
        first.dispose()
        second.dispose()


def test_run_migrations_uses_engine_database_url(monkeypatch):
    from app.config import settings
    from app.services import migrations

    captured: dict[str, str] = {}

    def fake_upgrade(cfg, revision):
        captured["url"] = cfg.get_main_option("sqlalchemy.url")

    monkeypatch.setattr(settings, "database_url", "sqlite+libsql:///./data/trade.db")
    monkeypatch.setattr(settings, "turso_sync_url", "libsql://mydb-org.turso.io")
    monkeypatch.setattr(settings, "turso_embedded_replica", False)
    monkeypatch.setattr(migrations.command, "upgrade", fake_upgrade)

    migrations.run_migrations()

    assert captured["url"] == "sqlite+libsql://mydb-org.turso.io?secure=true"

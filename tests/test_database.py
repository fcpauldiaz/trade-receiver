from app.config import settings
from app.database import _build_engine, normalized_database_url


def test_normalize_postgres_url_to_psycopg():
    assert (
        normalized_database_url("postgresql://trade:trade@localhost:5432/trade")
        == "postgresql+psycopg://trade:trade@localhost:5432/trade"
    )


def test_normalize_postgres_scheme_alias():
    assert (
        normalized_database_url("postgres://trade:trade@localhost:5432/trade")
        == "postgresql+psycopg://trade:trade@localhost:5432/trade"
    )


def test_normalize_psycopg_url_unchanged():
    url = "postgresql+psycopg://trade:trade@localhost:5432/trade"
    assert normalized_database_url(url) == url


def test_normalize_preserves_query_params():
    assert (
        normalized_database_url(
            "postgresql://trade:trade@localhost:5432/trade?sslmode=require"
        )
        == "postgresql+psycopg://trade:trade@localhost:5432/trade?sslmode=require"
    )


def test_normalize_rejects_libsql():
    try:
        normalized_database_url("libsql://db-org.turso.io")
    except ValueError as exc:
        assert "postgresql" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_build_engine_postgres(monkeypatch):
    monkeypatch.setattr(
        settings, "database_url", "postgresql://trade:trade@localhost:5432/trade"
    )
    engine = _build_engine()
    try:
        assert engine.dialect.name == "postgresql"
    finally:
        engine.dispose()


def test_build_engine_creates_independent_instances(monkeypatch):
    monkeypatch.setattr(
        settings, "database_url", "postgresql://trade:trade@localhost:5432/trade"
    )
    first = _build_engine()
    second = _build_engine()
    try:
        assert first is not second
    finally:
        first.dispose()
        second.dispose()

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Generator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings

logger = logging.getLogger(__name__)

_libsql_lock = threading.RLock()
_engine: Engine | None = None
SessionLocal: sessionmaker[Session]


def normalized_database_url(url: str | None = None) -> str:
    raw = settings.database_url if url is None else url

    if raw.startswith("libsql://"):
        raw = "sqlite+libsql://" + raw.removeprefix("libsql://")

    if not raw.startswith("sqlite+libsql://"):
        return raw

    parsed = urlparse(raw)
    if parsed.netloc == "" and parsed.path.startswith("/"):
        return raw

    query = parse_qs(parsed.query, keep_blank_values=True)
    if "secure" not in query:
        query["secure"] = ["true"]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def uses_libsql(url: str | None = None) -> bool:
    resolved = engine_database_url() if url is None else normalized_database_url(url)
    return "libsql" in resolved


def uses_embedded_libsql_replica(url: str | None = None) -> bool:
    resolved = engine_database_url() if url is None else normalized_database_url(url)
    return (
        settings.turso_embedded_replica
        and bool(settings.turso_sync_url)
        and resolved.startswith("sqlite+libsql:///")
    )


def _sanitize_url_for_log(url: str) -> str:
    return re.sub(r"(//)[^/@]+@", r"//***@", url)


def database_runtime_summary() -> dict[str, object]:
    resolved = engine_database_url()
    return {
        "configured_url": _sanitize_url_for_log(normalized_database_url()),
        "engine_url": _sanitize_url_for_log(resolved),
        "uses_libsql": uses_libsql(resolved),
        "embedded_replica": uses_embedded_libsql_replica(resolved),
        "sync_url_configured": bool((settings.turso_sync_url or "").strip()),
        "auth_token_configured": bool(settings.turso_auth_token),
    }


def log_database_runtime_config() -> None:
    summary = database_runtime_summary()
    logger.info(
        "database runtime config: engine_url=%s libsql=%s embedded_replica=%s "
        "sync_url_configured=%s auth_token_configured=%s",
        summary["engine_url"],
        summary["uses_libsql"],
        summary["embedded_replica"],
        summary["sync_url_configured"],
        summary["auth_token_configured"],
    )
    if summary["embedded_replica"]:
        logger.warning(
            "TURSO_EMBEDDED_REPLICA is enabled; libsql sync_url is active and may "
            "panic under concurrent webhook traffic"
        )
    elif summary["sync_url_configured"] and summary["uses_libsql"]:
        logger.info(
            "TURSO_SYNC_URL is set but embedded replica is disabled; using remote Turso only"
        )


def _is_libsql_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, (OperationalError, DBAPIError)):
        return True
    name = type(exc).__name__
    return "Panic" in name or "libsql" in str(exc).lower()


def _attach_libsql_error_handler(engine: Engine) -> None:
    if not uses_libsql():
        return

    @event.listens_for(engine, "handle_error")
    def _handle_libsql_error(exception_context) -> None:
        exc = exception_context.original_exception
        if exc is None or not _is_libsql_disconnect(exc):
            return
        exception_context.is_disconnect = True
        logger.warning("libsql connection error: %s", exc)


def engine_database_url() -> str:
    """Resolve the SQLAlchemy URL used by the engine.

    libsql embedded replicas (local file + sync_url) can Rust-panic under
    concurrent FastAPI traffic. Default to remote-only Turso when TURSO_SYNC_URL
    is set unless TURSO_EMBEDDED_REPLICA=true.
    """
    configured = normalized_database_url()
    remote = (settings.turso_sync_url or "").strip()
    if (
        remote
        and configured.startswith("sqlite+libsql:///")
        and not settings.turso_embedded_replica
    ):
        logger.info(
            "Using remote Turso URL for SQLAlchemy (embedded replica disabled; "
            "set TURSO_EMBEDDED_REPLICA=true to opt in)"
        )
        return normalized_database_url(remote)
    return configured


def database_connect_args(url: str | None = None) -> dict:
    resolved = engine_database_url() if url is None else normalized_database_url(url)
    connect_args: dict = {}
    if settings.turso_auth_token and "libsql" in resolved:
        connect_args["auth_token"] = settings.turso_auth_token
    if uses_embedded_libsql_replica(resolved):
        connect_args["sync_url"] = settings.turso_sync_url
    if "libsql" in resolved:
        connect_args["_check_same_thread"] = False
    return connect_args


def _register_libsql_dialect() -> None:
    from sqlalchemy.dialects import registry

    registry.register("sqlite.libsql", "app.libsql_dialect", "SQLiteDialect_libsql")


def _ensure_libsql_dialect(url: str) -> None:
    if "libsql" not in url:
        return
    try:
        import libsql  # noqa: F401, PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "DATABASE_URL uses libsql/Turso but the libsql package is not installed."
        ) from exc
    _register_libsql_dialect()


def _pool_kwargs_for_url(url: str) -> dict:
    if url.startswith("sqlite") or "libsql" in url:
        return {"poolclass": NullPool}
    return {}


def _build_engine() -> Engine:
    url = engine_database_url()
    _ensure_libsql_dialect(url)
    built = create_engine(
        url,
        connect_args=database_connect_args(url),
        **_pool_kwargs_for_url(url),
    )
    _attach_libsql_error_handler(built)
    return built


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def recreate_engine() -> Engine:
    """Dispose and rebuild the global engine (tests / recovery)."""
    global _engine, SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = _build_engine()
    SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


engine = get_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    lock = _libsql_lock if uses_libsql() else None
    if lock is not None:
        lock.acquire()
    db = SessionLocal()
    try:
        yield db
    except Exception as exc:
        db.rollback()
        if uses_libsql() and _is_libsql_disconnect(exc):
            recreate_engine()
        raise
    finally:
        db.close()
        if lock is not None:
            lock.release()

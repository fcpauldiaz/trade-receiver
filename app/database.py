from collections.abc import Generator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


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


def database_connect_args(url: str | None = None) -> dict:
    resolved = normalized_database_url(url)
    connect_args: dict = {}
    if settings.turso_auth_token and "libsql" in resolved:
        connect_args["auth_token"] = settings.turso_auth_token
    if settings.turso_sync_url and resolved.startswith("sqlite+libsql:///"):
        connect_args["sync_url"] = settings.turso_sync_url
    return connect_args


def _ensure_libsql_dialect(url: str) -> None:
    if "libsql" not in url:
        return
    try:
        import sqlalchemy_libsql  # noqa: F401, PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "DATABASE_URL uses libsql/Turso but sqlalchemy-libsql is not installed."
        ) from exc


def _build_engine():
    url = normalized_database_url()
    _ensure_libsql_dialect(url)
    return create_engine(url, connect_args=database_connect_args())


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

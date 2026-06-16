from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


def _ensure_dialect(url: str) -> None:
    if "libsql" not in url:
        return
    try:
        import sqlalchemy_libsql  # noqa: F401, PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "DATABASE_URL uses libsql but sqlalchemy-libsql is not installed. "
            "Use sqlite:///./data/trade.db for local SQLite, or ensure requirements are installed."
        ) from exc


def _build_engine():
    url = settings.database_url
    _ensure_dialect(url)
    connect_args: dict = {}
    if settings.turso_auth_token and "libsql" in url:
        connect_args["auth_token"] = settings.turso_auth_token
    return create_engine(url, connect_args=connect_args)


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

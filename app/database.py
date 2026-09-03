from collections.abc import Generator
from urllib.parse import urlparse, urlunparse
import logging
import re

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

_POSTGRES_SCHEMES = {"postgres", "postgresql", "postgresql+psycopg"}


def normalized_database_url(url: str | None = None) -> str:
    raw = (settings.database_url if url is None else url).strip()
    parsed = urlparse(raw)
    if parsed.scheme not in _POSTGRES_SCHEMES:
        raise ValueError(
            "DATABASE_URL must be postgresql://, postgres://, or postgresql+psycopg://."
        )
    if parsed.scheme in {"postgres", "postgresql"}:
        return urlunparse(parsed._replace(scheme="postgresql+psycopg"))
    return raw


def engine_database_url() -> str:
    return normalized_database_url()


def _sanitize_url_for_log(url: str) -> str:
    return re.sub(r"(//)[^/@]+@", r"//***@", url)


def log_database_runtime_config() -> None:
    logger.info(
        "database runtime config: engine_url=%s",
        _sanitize_url_for_log(engine_database_url()),
    )


def _build_engine() -> Engine:
    return create_engine(
        normalized_database_url(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

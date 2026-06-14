from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


def _build_engine():
    url = settings.database_url
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

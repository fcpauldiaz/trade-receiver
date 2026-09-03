from __future__ import annotations

import os

_raw = os.environ.get("DATABASE_URL", "")
if not _raw.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
    os.environ["DATABASE_URL"] = "postgresql://trade:trade@localhost:5432/trade"

os.environ["OPENAI_API_KEY"] = ""
os.environ["AI_GATEWAY_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import Base, SessionLocal, engine, get_db
from app.main import app
from app.models import tables as _tables  # noqa: F401
from app.config import settings

settings.openai_api_key = None
settings.ai_gateway_api_key = None


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def _truncate_tables():
    with engine.begin() as conn:
        quoted = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
        if quoted:
            conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def db_factory():
    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return SessionLocal


@pytest.fixture()
def client(db_factory):
    return TestClient(app), db_factory


@pytest.fixture()
def db_session(db_factory):
    db = db_factory()
    yield db
    db.close()


@pytest.fixture()
def http_client(db_factory):
    return TestClient(app)

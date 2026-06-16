import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.tables import Subscription, User
from tests.test_e2e import FakeAdapter, _seed_paid_user


@pytest.fixture()
def db_session(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    async def fake_get_adapter(db, conn):
        return FakeAdapter()

    monkeypatch.setattr("app.api.ingest.get_adapter", fake_get_adapter)
    db = SessionLocal()
    yield db
    db.close()
    app.dependency_overrides.clear()


@pytest.fixture()
def ingest_client(db_session):
    return TestClient(app)


def test_ingest_requires_auth(ingest_client):
    res = ingest_client.post("/v1/ingest", json={"title": "Alert", "body": "BTO SPY 580C"})
    assert res.status_code == 401


def test_ingest_invalid_token(ingest_client):
    res = ingest_client.post(
        "/v1/ingest",
        json={"title": "Alert", "body": "BTO SPY 580C"},
        headers={"Authorization": "Bearer bad-token"},
    )
    assert res.status_code == 401


def test_ingest_active_subscription(ingest_client, db_session: Session):
    user, _ = _seed_paid_user(db_session)
    token = "test-api-key"
    user.api_key_hash = hashlib.sha256(token.encode()).hexdigest()
    db_session.commit()

    res = ingest_client.post(
        "/v1/ingest",
        json={"title": "Alert", "body": "BTO SPY 580C 6/20 @ 2.50"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["status"] in {"filled", "skipped", "rejected", "duplicate", "validation_failed"}


def test_ingest_inactive_subscription(ingest_client, db_session: Session):
    token = secrets.token_urlsafe(16)
    user = User(
        email="free@example.com",
        api_key_hash=hashlib.sha256(token.encode()).hexdigest(),
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(Subscription(user_id=user.id, status="none", plan_name="free"))
    db_session.commit()

    res = ingest_client.post(
        "/v1/ingest",
        json={"title": "Alert", "body": "BTO SPY 580C"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 402

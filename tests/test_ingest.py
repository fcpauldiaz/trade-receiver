import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.tables import InboundAlert, Subscription, User
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
    monkeypatch.setattr("app.services.market_hours.is_rth", lambda now=None: True)
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
    stored = db_session.query(InboundAlert).all()
    assert len(stored) == 1
    assert stored[0].processed is True
    assert stored[0].skip_reason


def test_alerts_requires_auth(ingest_client):
    res = ingest_client.get("/v1/me/alerts")
    assert res.status_code == 401


def test_alerts_lists_skipped_capture(ingest_client, db_session: Session, monkeypatch):
    user, _ = _seed_paid_user(db_session)
    token = "test-api-key"
    user.api_key_hash = hashlib.sha256(token.encode()).hexdigest()
    db_session.commit()
    monkeypatch.setattr("app.services.market_hours.is_rth", lambda now=None: False)

    ingest = ingest_client.post(
        "/v1/ingest",
        json={
            "app_id": "com.hnc.Discord",
            "title": "Alerts",
            "body": "BTO SPY 580C 6/20 @ 2.50",
            "platform": "macos",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ingest.status_code == 200

    res = ingest_client.get("/v1/me/alerts", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    item = items[0]
    assert item["outcome"] == "skipped"
    assert item["source_app"] == "com.hnc.Discord"
    assert item["platform"] == "macos"
    assert item["title"] == "Alerts"
    assert "BTO SPY" in item["text"]
    assert "raw_payload" not in item
    assert item["trade_id"] is None


def test_alerts_are_user_scoped(ingest_client, db_session: Session):
    user_a, _ = _seed_paid_user(db_session)
    token_a = "test-api-key"
    user_a.api_key_hash = hashlib.sha256(token_a.encode()).hexdigest()
    other = User(
        email="other@example.com",
        api_key_hash=hashlib.sha256(b"other-key").hexdigest(),
    )
    db_session.add(other)
    db_session.flush()
    db_session.add(
        InboundAlert(
            user_id=other.id,
            idempotency_key="secret-other",
            raw_payload='{"title":"Secret","body":"do not leak"}',
            normalized_text="Secret\ndo not leak",
        )
    )
    db_session.add(
        InboundAlert(
            user_id=user_a.id,
            idempotency_key="mine",
            raw_payload='{"title":"Mine","body":"BTO SPY","app_id":"discord"}',
            normalized_text="Mine\nBTO SPY",
            skip_reason="no broker connected",
            processed=True,
        )
    )
    db_session.commit()

    res = ingest_client.get("/v1/me/alerts", headers={"Authorization": f"Bearer {token_a}"})
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["title"] == "Mine"
    assert items[0]["text"] != "Secret\ndo not leak"


def test_ingest_skips_outside_rth(ingest_client, db_session: Session, monkeypatch):
    user, _ = _seed_paid_user(db_session)
    token = "test-api-key"
    user.api_key_hash = hashlib.sha256(token.encode()).hexdigest()
    db_session.commit()

    monkeypatch.setattr("app.services.market_hours.is_rth", lambda now=None: False)

    res = ingest_client.post(
        "/v1/ingest",
        json={"title": "Alert", "body": "BTO SPY 580C 6/20 @ 2.50"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "skipped"
    assert "regular trading hours" in data["reason"]

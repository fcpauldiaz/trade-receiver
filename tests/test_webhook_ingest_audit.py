import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.tables import BrokerConnection, Subscription, User, WebhookIngestEvent
from app.services.webhook_ingest_audit import (
    MAX_WEBHOOK_INGEST_PAYLOAD_BYTES,
    serialize_webhook_payload,
)
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

    monkeypatch.setattr("app.services.ingest_pipeline.get_adapter", fake_get_adapter)
    monkeypatch.setattr("app.services.market_hours.is_rth", lambda now=None: True)
    db = SessionLocal()
    yield db
    db.close()
    app.dependency_overrides.clear()


@pytest.fixture()
def client(db_session):
    return TestClient(app)


def test_webhook_ingest_recorded_in_alerts_audit(client, db_session: Session):
    user, _ = _seed_paid_user(db_session)
    token = "user-api-key"
    user.api_key_hash = hashlib.sha256(token.encode()).hexdigest()
    db_session.commit()

    create = client.post(
        "/v1/me/webhooks",
        json={"name": "TradingView"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 200
    webhook = create.json()

    payload = {
        "app_id": "tradingview",
        "title": "SPY Alert",
        "body": "BTO SPY 580C 6/20 @ 2.50",
        "platform": "web",
    }
    ok = client.post(f"/v1/webhooks/{webhook['id']}", json=payload)
    assert ok.status_code == 200

    events = db_session.query(WebhookIngestEvent).filter_by(user_id=user.id).all()
    assert len(events) == 1
    event = events[0]
    assert event.inbound_webhook_id == webhook["id"]
    assert event.status in {"filled", "skipped", "submitted", "validation_failed"}
    assert event.alert_id is not None
    assert json.loads(event.request_payload)["title"] == "SPY Alert"

    audit = client.get("/v1/me/alerts", headers={"Authorization": f"Bearer {token}"})
    assert audit.status_code == 200
    items = audit.json()
    assert len(items) == 1
    item = items[0]
    assert item["source"] == "webhook"
    assert item["webhook_id"] == webhook["id"]
    assert item["webhook_name"] == "TradingView"
    assert item["ingest_status"] in {"filled", "skipped", "submitted", "validation_failed"}
    assert item["payload"]["title"] == "SPY Alert"
    assert item["alert_id"] == event.alert_id
    assert "raw_payload" not in item


def test_webhook_duplicate_creates_audit_event(client, db_session: Session):
    user, _ = _seed_paid_user(db_session)
    token = "user-api-key"
    user.api_key_hash = hashlib.sha256(token.encode()).hexdigest()
    db_session.commit()

    create = client.post(
        "/v1/me/webhooks",
        json={"name": "Dup"},
        headers={"Authorization": f"Bearer {token}"},
    )
    webhook_id = create.json()["id"]
    payload = {"title": "Alert", "body": "BTO SPY 580C 6/20 @ 2.50"}

    first = client.post(f"/v1/webhooks/{webhook_id}", json=payload)
    second = client.post(f"/v1/webhooks/{webhook_id}", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    events = (
        db_session.query(WebhookIngestEvent)
        .filter_by(user_id=user.id, inbound_webhook_id=webhook_id)
        .order_by(WebhookIngestEvent.created_at.asc())
        .all()
    )
    assert len(events) == 2
    assert events[1].status == "duplicate"
    assert events[1].alert_id == events[0].alert_id

    audit = client.get("/v1/me/alerts", headers={"Authorization": f"Bearer {token}"})
    assert audit.status_code == 200
    items = audit.json()
    assert len(items) == 2
    ingest_statuses = {item["ingest_status"] for item in items}
    assert "duplicate" in ingest_statuses


def test_webhook_ingest_events_are_user_scoped(client, db_session: Session):
    user_a, _ = _seed_paid_user(db_session)
    token_a = "user-a-key"
    user_a.api_key_hash = hashlib.sha256(token_a.encode()).hexdigest()

    token_b = "user-b-key"
    user_b = User(
        email="other@example.com",
        api_key_hash=hashlib.sha256(token_b.encode()).hexdigest(),
        max_contracts=10,
    )
    db_session.add(user_b)
    db_session.flush()
    db_session.add(Subscription(user_id=user_b.id, status="active", plan_name="pro"))
    db_session.add(
        BrokerConnection(
            user_id=user_b.id,
            broker="tradier",
            status="connected",
            account_id="VA999",
            encrypted_credentials="token",
        )
    )
    db_session.commit()

    webhook_a = client.post(
        "/v1/me/webhooks",
        json={"name": "A"},
        headers={"Authorization": f"Bearer {token_a}"},
    ).json()
    webhook_b = client.post(
        "/v1/me/webhooks",
        json={"name": "B"},
        headers={"Authorization": f"Bearer {token_b}"},
    ).json()

    client.post(
        f"/v1/webhooks/{webhook_a['id']}",
        json={"title": "A", "body": "BTO SPY 580C 6/20 @ 2.50"},
    )
    client.post(
        f"/v1/webhooks/{webhook_b['id']}",
        json={"title": "B", "body": "BTO QQQ 400C 6/20 @ 1.50"},
    )

    audit_a = client.get("/v1/me/alerts", headers={"Authorization": f"Bearer {token_a}"})
    audit_b = client.get("/v1/me/alerts", headers={"Authorization": f"Bearer {token_b}"})
    assert len(audit_a.json()) == 1
    assert len(audit_b.json()) == 1
    assert audit_a.json()[0]["webhook_name"] == "A"
    assert audit_b.json()[0]["webhook_name"] == "B"


def test_direct_ingest_still_lists_as_ingest_source(client, db_session: Session):
    user, _ = _seed_paid_user(db_session)
    token = "user-api-key"
    user.api_key_hash = hashlib.sha256(token.encode()).hexdigest()
    db_session.commit()

    client.post(
        "/v1/ingest",
        json={"title": "Alert", "body": "BTO SPY 580C 6/20 @ 2.50"},
        headers={"Authorization": f"Bearer {token}"},
    )

    audit = client.get("/v1/me/alerts", headers={"Authorization": f"Bearer {token}"})
    item = audit.json()[0]
    assert item["source"] == "ingest"
    assert item["webhook_id"] is None
    assert item["payload"]["title"] == "Alert"


def test_serialize_webhook_payload_truncates_large_body():
    body = {"title": "x", "body": "y" * MAX_WEBHOOK_INGEST_PAYLOAD_BYTES}
    stored = serialize_webhook_payload(body)
    assert len(stored.encode("utf-8")) <= MAX_WEBHOOK_INGEST_PAYLOAD_BYTES + 128
    parsed = json.loads(stored)
    assert parsed["_truncated"] is True
    assert parsed["_max_bytes"] == MAX_WEBHOOK_INGEST_PAYLOAD_BYTES

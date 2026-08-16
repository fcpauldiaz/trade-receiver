import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.tables import Subscription, User
from app.services.creem import product_id_for_plan
from app.services.entitlements import (
    can_process_trades,
    creem_status_from_event,
    verify_creem_signature,
)
from app.services.jwt_auth import hash_api_key


@pytest.fixture()
def client():
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
    yield TestClient(app), SessionLocal
    app.dependency_overrides.clear()


def test_creem_signature():
    secret = "whsec_test"
    payload = b'{"eventType":"subscription.paid"}'
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert verify_creem_signature(payload, sig, secret)
    assert not verify_creem_signature(payload, "bad", secret)


def test_can_process_trades_trialing():
    user = User(email="trial@example.com")
    user.subscription = Subscription(status="trialing")
    assert can_process_trades(user) is True


def test_creem_status_mapping():
    assert creem_status_from_event("subscription.paid") == "active"
    assert creem_status_from_event("subscription.trialing") == "trialing"
    assert creem_status_from_event("subscription.canceled") == "cancelled"
    assert creem_status_from_event("subscription.scheduled_cancel") == "scheduled_cancel"


def test_checkout_requires_creem_config(client):
    test_client, SessionLocal = client
    db = SessionLocal()
    user = User(email="checkout@example.com")
    db.add(user)
    db.commit()
    token = "dev-key"
    user.api_key_hash = hash_api_key(token)
    db.commit()
    db.close()

    original_key = settings.creem_api_key
    original_product = settings.creem_product_id
    settings.creem_api_key = None
    settings.creem_product_id = None
    try:
        response = test_client.post(
            "/v1/me/billing/checkout",
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        settings.creem_api_key = original_key
        settings.creem_product_id = original_product

    assert response.status_code == 503


def test_product_id_for_plan(monkeypatch):
    monkeypatch.setattr(settings, "creem_product_id", "prod_month")
    monkeypatch.setattr(settings, "creem_yearly_product_id", "prod_year")
    assert product_id_for_plan("monthly") == "prod_month"
    assert product_id_for_plan("yearly") == "prod_year"


def test_checkout_uses_yearly_product(client, monkeypatch):
    test_client, SessionLocal = client
    db = SessionLocal()
    user = User(email="yearly@example.com")
    db.add(user)
    db.commit()
    token = "dev-key"
    user.api_key_hash = hash_api_key(token)
    db.commit()
    db.close()

    captured: dict = {}

    def fake_checkout(**kwargs):
        captured.update(kwargs)
        return {"checkout_url": "https://pay.example/c", "id": "chk_1"}

    monkeypatch.setattr("app.api.billing.create_checkout", fake_checkout)
    monkeypatch.setattr(settings, "creem_api_key", "creem_test_x")
    monkeypatch.setattr(settings, "creem_product_id", "prod_month")
    monkeypatch.setattr(settings, "creem_yearly_product_id", "prod_year")

    response = test_client.post(
        "/v1/me/billing/checkout",
        json={"plan": "yearly"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert captured["product_id"] == "prod_year"
    assert captured["metadata"]["plan"] == "yearly"


def test_checkout_yearly_requires_product_id(client, monkeypatch):
    test_client, SessionLocal = client
    db = SessionLocal()
    user = User(email="noyear@example.com")
    db.add(user)
    db.commit()
    token = "dev-key"
    user.api_key_hash = hash_api_key(token)
    db.commit()
    db.close()

    monkeypatch.setattr(settings, "creem_api_key", "creem_test_x")
    monkeypatch.setattr(settings, "creem_product_id", "prod_month")
    monkeypatch.setattr(settings, "creem_yearly_product_id", None)

    response = test_client.post(
        "/v1/me/billing/checkout",
        json={"plan": "yearly"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 503
    assert "CREEM_YEARLY_PRODUCT_ID" in response.json()["detail"]


def test_creem_webhook_grants_access(client):
    test_client, SessionLocal = client
    db = SessionLocal()
    user = User(email="creem@example.com")
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()

    payload = {
        "id": "evt_test_creem_1",
        "eventType": "subscription.paid",
        "object": {
            "id": "sub_test_1",
            "object": "subscription",
            "status": "active",
            "customer": {"id": "cust_test_1", "email": "creem@example.com"},
            "product": {"id": "prod_test_1", "name": "Pro"},
            "metadata": {"user_id": user_id, "referenceId": user_id},
            "next_transaction_date": "2026-08-28T12:00:00.000Z",
            "current_period_end_date": "2026-08-28T12:00:00.000Z",
        },
    }
    body = json.dumps(payload).encode()
    secret = "creem-webhook-secret"
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    original = settings.creem_webhook_secret
    settings.creem_webhook_secret = secret
    try:
        response = test_client.post(
            "/v1/webhooks/creem",
            content=body,
            headers={"Content-Type": "application/json", "creem-signature": sig},
        )
    finally:
        settings.creem_webhook_secret = original

    assert response.status_code == 200
    assert response.json()["ok"] is True

    db = SessionLocal()
    stored = db.get(User, user_id)
    assert stored is not None
    assert stored.subscription is not None
    assert stored.subscription.status == "active"
    assert stored.subscription.creem_customer_id == "cust_test_1"
    assert stored.subscription.creem_subscription_id == "sub_test_1"
    assert can_process_trades(stored) is True
    db.close()

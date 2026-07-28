import hashlib
import secrets
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.tables import BrokerConnection, Subscription, User


@pytest.fixture()
def client(monkeypatch):
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


def _create_user(db, *, active: bool) -> tuple[User, str]:
    token = secrets.token_urlsafe(32)
    user = User(
        email="paid@example.com",
        api_key_hash=hashlib.sha256(token.encode()).hexdigest(),
        default_mode="paper",
    )
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id, status="active" if active else "none", plan_name="pro"))
    db.commit()
    return user, token


def test_tradier_token_connect(client, monkeypatch):
    test_client, SessionLocal = client
    db = SessionLocal()
    user, token = _create_user(db, active=True)
    user_id = user.id
    db.close()

    monkeypatch.setattr(
        "app.api.brokers.TradierAdapter.fetch_primary_account_id",
        AsyncMock(return_value="VA999"),
    )

    resp = test_client.post(
        "/v1/me/brokers/tradier/token",
        json={"access_token": "sandbox-token-abc", "environment": "sandbox"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["broker"] == "tradier"
    assert data["status"] == "connected"
    assert data["account_id"] == "VA999"
    assert data["environment"] == "sandbox"

    db = SessionLocal()
    conn = db.query(BrokerConnection).filter_by(user_id=user_id, broker="tradier").first()
    assert conn is not None
    assert conn.status == "connected"
    assert conn.account_id == "VA999"
    assert conn.environment == "sandbox"
    user = db.get(User, user_id)
    assert user is not None
    assert user.default_mode == "paper"
    db.close()


def test_tradier_token_connect_live(client, monkeypatch):
    test_client, SessionLocal = client
    db = SessionLocal()
    user, token = _create_user(db, active=True)
    user_id = user.id
    db.close()

    monkeypatch.setattr(
        "app.api.brokers.TradierAdapter.fetch_primary_account_id",
        AsyncMock(return_value="6YA00000"),
    )

    resp = test_client.post(
        "/v1/me/brokers/tradier/token",
        json={"access_token": "live-token-abc", "environment": "live"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["environment"] == "live"

    db = SessionLocal()
    conn = db.query(BrokerConnection).filter_by(user_id=user_id, broker="tradier").first()
    assert conn is not None
    assert conn.environment == "live"
    user = db.get(User, user_id)
    assert user is not None
    assert user.default_mode == "live"
    assert user.live_trading_enabled is True
    db.close()


def test_tradier_token_switch_environment(client, monkeypatch):
    test_client, SessionLocal = client
    db = SessionLocal()
    user, token = _create_user(db, active=True)
    user_id = user.id
    db.close()

    monkeypatch.setattr(
        "app.api.brokers.TradierAdapter.fetch_primary_account_id",
        AsyncMock(side_effect=["VA999", "6YA00000"]),
    )

    assert test_client.post(
        "/v1/me/brokers/tradier/token",
        json={"access_token": "sandbox-token", "environment": "sandbox"},
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 200

    resp = test_client.post(
        "/v1/me/brokers/tradier/token",
        json={"access_token": "live-token", "environment": "live"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["environment"] == "live"

    db = SessionLocal()
    conn = db.query(BrokerConnection).filter_by(user_id=user_id, broker="tradier").first()
    assert conn is not None
    assert conn.environment == "live"
    assert conn.account_id == "6YA00000"
    db.close()


def test_tradier_token_connect_rejects_bad_token(client, monkeypatch):
    test_client, SessionLocal = client
    db = SessionLocal()
    _, token = _create_user(db, active=True)
    db.close()

    monkeypatch.setattr(
        "app.api.brokers.TradierAdapter.fetch_primary_account_id",
        AsyncMock(return_value=None),
    )

    resp = test_client.post(
        "/v1/me/brokers/tradier/token",
        json={"access_token": "bad", "environment": "sandbox"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_tradier_token_requires_subscription(client):
    test_client, SessionLocal = client
    db = SessionLocal()
    _, token = _create_user(db, active=False)
    db.close()

    resp = test_client.post(
        "/v1/me/brokers/tradier/token",
        json={"access_token": "sandbox-token-abc", "environment": "sandbox"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 402

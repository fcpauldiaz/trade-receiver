import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.tables import BrokerConnection, Subscription, User
from app.services.crypto import encrypt_value


class FakeBrokerAdapter:
    name = "tradier"

    async def place_equity_order(self, symbol: str, quantity: int, side: str, mode: str):
        from app.brokers.base import OrderResult
        return OrderResult(
            success=True,
            order_id=f"paper-{symbol}",
            fill_price=None,
            raw_response={"simulated": True, "mode": mode, "symbol": symbol, "quantity": quantity},
        )


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
    import app.api.brokers as brokers_api

    async def _fake_adapter(db, conn):
        return FakeBrokerAdapter()

    brokers_api.get_adapter = _fake_adapter
    yield TestClient(app), SessionLocal
    app.dependency_overrides.clear()


def _create_user(db, *, active: bool, live: bool = False) -> tuple[User, str]:
    token = secrets.token_urlsafe(32)
    user = User(
        email="paid@example.com",
        api_key_hash=hashlib.sha256(token.encode()).hexdigest(),
        webhook_enabled=active,
        default_mode="paper",
        live_trading_enabled=live,
    )
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id, status="active" if active else "none", plan_name="pro"))
    db.commit()
    return user, token


def test_test_order_requires_auth(client):
    test_client, _ = client
    resp = test_client.post("/v1/me/brokers/tradier/test-order", json={})
    assert resp.status_code == 401


def test_test_order_requires_subscription(client):
    test_client, SessionLocal = client
    db = SessionLocal()
    _, token = _create_user(db, active=False)
    db.close()
    resp = test_client.post(
        "/v1/me/brokers/tradier/test-order",
        json={"symbol": "SPY", "quantity": 1, "side": "buy"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 402


def test_test_order_paper_simulated(client):
    test_client, SessionLocal = client
    db = SessionLocal()
    user, token = _create_user(db, active=True)
    db.add(
        BrokerConnection(
            user_id=user.id,
            broker="tradier",
            status="connected",
            account_id="VA123",
            encrypted_credentials=encrypt_value("fake-token"),
        )
    )
    db.commit()
    db.close()

    resp = test_client.post(
        "/v1/me/brokers/tradier/test-order",
        json={"symbol": "SPY", "quantity": 1, "side": "buy"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["simulated"] is True
    assert data["broker"] == "tradier"


def test_test_order_live_blocked_without_flag(client):
    test_client, SessionLocal = client
    db = SessionLocal()
    user, token = _create_user(db, active=True, live=False)
    user.default_mode = "live"
    db.add(
        BrokerConnection(
            user_id=user.id,
            broker="tradier",
            status="connected",
            account_id="VA123",
            encrypted_credentials=encrypt_value("fake-token"),
        )
    )
    db.commit()
    db.close()

    resp = test_client.post(
        "/v1/me/brokers/tradier/test-order",
        json={"symbol": "SPY", "quantity": 1, "side": "buy"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400

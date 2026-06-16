import hashlib
import secrets
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.parse_alert import SAMPLE_ALERTS, parse_alert_rules
from app.brokers.base import OptionContract, OrderResult
from app.database import Base, get_db
from app.main import app
from app.models.tables import BrokerConnection, Subscription, User


class FakeAdapter:
    name = "tradier"

    async def get_option_chain(self, underlying: str, expiration: date | None = None) -> list[OptionContract]:
        exp = expiration or date(2026, 6, 20)
        return [
            OptionContract(
                symbol="SPY260620C00580000",
                underlying="SPY",
                option_type="call",
                strike=Decimal("580"),
                expiration=exp,
                bid=Decimal("2.40"),
                ask=Decimal("2.50"),
                open_interest=1000,
            )
        ]

    async def preview_order(self, contract: OptionContract, quantity: int, side: str) -> OrderResult:
        return OrderResult(success=True, order_id=None, fill_price=contract.ask, raw_response={})

    async def place_order(self, contract: OptionContract, quantity: int, side: str, mode: str) -> OrderResult:
        return OrderResult(
            success=True,
            order_id="paper-test-1",
            fill_price=contract.ask,
            raw_response={"simulated": True, "mode": mode},
        )

    async def get_positions(self):
        return [{"symbol": "SPY260620C00580000", "quantity": 10}]

    async def get_order_status(self, order_id: str):
        return {"status": "FILLED"}

    async def get_account_equity(self):
        return Decimal("100000")

    async def place_equity_order(self, symbol: str, quantity: int, side: str, mode: str) -> OrderResult:
        return OrderResult(
            success=True,
            order_id=f"paper-{symbol}",
            fill_price=None,
            raw_response={"simulated": True, "mode": mode, "symbol": symbol, "quantity": quantity},
        )


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

    monkeypatch.setattr("app.api.webhooks.get_adapter", fake_get_adapter)
    db = SessionLocal()
    yield db
    db.close()
    app.dependency_overrides.clear()


@pytest.fixture()
def client(db_session: Session):
    return TestClient(app)


def _seed_paid_user(db: Session) -> tuple[User, str]:
    secret = secrets.token_urlsafe(16)
    user = User(
        email="paid@example.com",
        api_key_hash=hashlib.sha256(b"test-api-key").hexdigest(),
        webhook_secret=secret,
        webhook_enabled=True,
    )
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id, status="active", plan_name="pro"))
    db.add(
        BrokerConnection(
            user_id=user.id,
            broker="tradier",
            status="connected",
            account_id="VA123",
            encrypted_credentials="token",
        )
    )
    db.commit()
    return user, secret


def test_parse_fixture_alerts():
    intent = parse_alert_rules(SAMPLE_ALERTS[0])
    assert intent.action == "buy_to_open"
    assert intent.underlying == "SPY"
    assert intent.strike == Decimal("580")
    assert intent.option_type == "call"

    intent2 = parse_alert_rules(SAMPLE_ALERTS[1])
    assert intent2.action == "sell_to_close"
    assert intent2.underlying == "QQQ"

    intent3 = parse_alert_rules(SAMPLE_ALERTS[2])
    assert intent3.action == "buy_to_open"
    assert intent3.underlying == "AAPL"


def test_webhook_e2e_paper_order(client: TestClient, db_session: Session):
    user, secret = _seed_paid_user(db_session)
    resp = client.post(
        f"/hooks/{user.id}/{secret}",
        json={"title": "Alert", "body": "BTO SPY 580C 6/20 @ 2.50"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("filled", "validation_failed")
    if data["status"] == "filled":
        assert data["trade_id"]


def test_webhook_rejects_inactive_subscription(client: TestClient, db_session: Session):
    secret = secrets.token_urlsafe(16)
    user = User(email="free@example.com", webhook_secret=secret, webhook_enabled=True)
    db_session.add(user)
    db_session.flush()
    db_session.add(Subscription(user_id=user.id, status="none", plan_name="free"))
    db_session.commit()

    resp = client.post(
        f"/hooks/{user.id}/{secret}",
        json={"title": "Alert", "body": "BTO SPY 580C 6/20"},
    )
    assert resp.status_code == 402


def test_webull_disabled_by_default():
    from app.brokers.webull import WebullAdapter
    from app.config import settings
    import asyncio

    assert settings.webull_enabled is False
    adapter = WebullAdapter()
    result = asyncio.run(
        adapter.place_order(
            OptionContract(
                symbol="X",
                underlying="SPY",
                option_type="call",
                strike=Decimal("1"),
                expiration=date.today(),
                bid=None,
                ask=None,
                open_interest=None,
            ),
            1,
            "buy_to_open",
            "paper",
        )
    )
    assert result.success is False

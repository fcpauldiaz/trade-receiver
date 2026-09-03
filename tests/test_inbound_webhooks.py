import hashlib
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.parse_alert import parse_alert, parse_alert_rules
from app.brokers.ninjatrader import NinjaTraderAdapter
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.tables import BrokerConnection, Subscription, User
from app.schemas.trade import TradeIntent
from app.services.crypto import encrypt_value
from app.services.futures_trade import (
    is_futures_order_payload,
    map_futures_order_payload,
    parse_futures_alert_rules,
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


def test_create_and_receive_webhook(client, db_session: Session):
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
    data = create.json()
    assert data["enabled"] is True
    assert "secret" not in data
    assert data["url"].endswith(f"/v1/webhooks/{data['id']}")

    ok = client.post(
        f"/v1/webhooks/{data['id']}",
        json={"title": "Alert", "body": "BTO SPY 580C 6/20 @ 2.50"},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] in {"filled", "skipped", "submitted", "validation_failed", "duplicate"}


def test_receive_webhook_unknown_id_returns_404(client):
    res = client.post(
        "/v1/webhooks/00000000-0000-0000-0000-000000000000",
        json={"title": "Alert", "body": "BTO SPY"},
    )
    assert res.status_code == 404


def test_structured_futures_payload_mapping():
    body = {
        "id": "alert-1",
        "symbol": "MES",
        "action": "BUY",
        "orderType": "MARKET",
        "quantity": 2,
        "stopLossTicks": 8,
        "profitTargetTicks": 16,
    }
    assert is_futures_order_payload(body)
    intent = map_futures_order_payload(body)
    assert intent.asset_class == "future"
    assert intent.underlying == "MES1!"
    assert intent.quantity == 2
    assert intent.stop_loss_ticks == 8
    assert intent.profit_target_ticks == 16


def test_parse_futures_rules():
    intent = parse_alert_rules("BUY MES 1 SL 10 TP 20")
    assert intent.asset_class == "future"
    assert intent.underlying == "MES1!"
    assert intent.action == "buy_to_open"
    assert intent.stop_loss_ticks == 10
    assert intent.profit_target_ticks == 20


@pytest.mark.asyncio
async def test_parse_alert_uses_gateway(monkeypatch):
    monkeypatch.setattr(settings, "ai_gateway_api_key", "gw-test")
    monkeypatch.setattr(settings, "openai_api_key", None)

    async def fake_chat(**kwargs):
        return TradeIntent(
            action="buy_to_open",
            underlying="SPY",
            strike=580,
            expiration=None,
            confidence=0.9,
            rationale="gateway parse",
        ).model_dump(mode="json")

    monkeypatch.setattr("app.agents.parse_alert.chat_json_completion", fake_chat)
    intent = await parse_alert("BTO SPY 580C 6/20")
    assert intent.underlying == "SPY"
    assert intent.rationale == "gateway parse"


@pytest.mark.asyncio
async def test_ninjatrader_forward_payload(monkeypatch):
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "order_id": "nt-1"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("app.brokers.ninjatrader.httpx.AsyncClient", lambda timeout: FakeClient())

    adapter = NinjaTraderAdapter(forward_url="https://tunnel.example/webhook", webhook_secret="out-secret")
    from app.schemas.trade import ValidatedFuturesTrade

    validated = ValidatedFuturesTrade(
        action="BUY",
        symbol="MES",
        quantity=1,
        stop_loss_ticks=10,
        profit_target_ticks=20,
        confidence=1.0,
        rationale="test",
        broker="ninjatrader",
        external_id="order-123",
    )
    result = await adapter.execute_futures_order(validated, mode="paper", dry_run=True)
    assert result.success is True
    assert captured["url"] == "https://tunnel.example/webhook"
    assert captured["headers"]["X-Webhook-Secret"] == "out-secret"
    assert captured["json"]["symbol"] == "MES"
    assert captured["json"]["action"] == "BUY"
    assert captured["json"]["dryRun"] is True


def test_ninjatrader_connect_and_futures_ingest(client, db_session: Session, monkeypatch):
    user, token = _seed_paid_user(db_session)
    user.api_key_hash = hashlib.sha256(token.encode()).hexdigest()
    user.default_broker = "ninjatrader"
    db_session.commit()

    connect = client.post(
        "/v1/me/brokers/ninjatrader/connect",
        json={
            "forward_url": "https://tunnel.example/webhook",
            "webhook_secret": "bridge-secret",
            "account_label": "Sim101",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert connect.status_code == 200
    assert connect.json()["broker"] == "ninjatrader"

    async def fake_get_adapter(db, conn):
        from app.services.crypto import decrypt_value

        raw = decrypt_value(conn.encrypted_credentials or "")
        return NinjaTraderAdapter.from_credentials(raw)

    monkeypatch.setattr("app.services.ingest_pipeline.get_adapter", fake_get_adapter)

    async def fake_execute(self, validated, *, mode="paper", dry_run=None, user_id=None):
        from app.brokers.base import OrderResult

        return OrderResult(
            success=True,
            order_id=validated.external_id,
            fill_price=None,
            raw_response={"simulated": True, "dry_run": dry_run},
        )

    monkeypatch.setattr(NinjaTraderAdapter, "execute_futures_order", fake_execute)

    res = client.post(
        "/v1/ingest",
        json={"symbol": "MES", "action": "BUY", "quantity": 1, "orderType": "MARKET"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "submitted"


def test_list_brokers_returns_ninjatrader_forward_url(client, db_session: Session):
    user, token = _seed_paid_user(db_session)
    user.api_key_hash = hashlib.sha256(token.encode()).hexdigest()
    db_session.commit()

    forward_url = "https://tunnel.example/webhook"
    connect = client.post(
        "/v1/me/brokers/ninjatrader/connect",
        json={
            "forward_url": forward_url,
            "webhook_secret": "bridge-secret",
            "account_label": "Sim101",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert connect.status_code == 200

    list_resp = client.get("/v1/me/brokers", headers={"Authorization": f"Bearer {token}"})
    assert list_resp.status_code == 200
    brokers = list_resp.json()
    nt = next(row for row in brokers if row["broker"] == "ninjatrader")
    assert nt["status"] == "connected"
    assert nt["forward_url"] == forward_url
    assert nt["account_id"] == "Sim101"
    assert "webhook_secret" not in nt


def test_parse_futures_rules_es1_continuous_symbol():
    intent = parse_futures_alert_rules("BUY ES1! 1")
    assert intent is not None
    assert intent.asset_class == "future"
    assert intent.underlying == "ES1!"
    assert intent.action == "buy_to_open"
    assert intent.quantity == 1


def test_structured_es1_payload_normalizes_symbol():
    body = {
        "symbol": "ES",
        "action": "BUY",
        "orderType": "MARKET",
        "quantity": 1,
    }
    intent = map_futures_order_payload(body)
    assert intent.underlying == "ES1!"


def test_futures_webhook_outside_rth_returns_skipped(client, db_session: Session, monkeypatch):
    user, token = _seed_paid_user(db_session)
    user.api_key_hash = hashlib.sha256(token.encode()).hexdigest()
    user.default_broker = "ninjatrader"
    from app.brokers.ninjatrader import pack_ninjatrader_credentials

    db_session.add(
        BrokerConnection(
            user_id=user.id,
            broker="ninjatrader",
            status="connected",
            encrypted_credentials=encrypt_value(pack_ninjatrader_credentials("", "bridge-secret")),
            account_id="Sim101",
        )
    )
    db_session.commit()

    async def fake_get_adapter(db, conn):
        from app.services.crypto import decrypt_value

        raw = decrypt_value(conn.encrypted_credentials or "")
        return NinjaTraderAdapter.from_credentials(raw)

    monkeypatch.setattr("app.services.ingest_pipeline.get_adapter", fake_get_adapter)
    monkeypatch.setattr("app.services.market_hours.is_rth", lambda now=None: False)

    create = client.post(
        "/v1/me/webhooks",
        json={"name": "Futures"},
        headers={"Authorization": f"Bearer {token}"},
    )
    webhook_id = create.json()["id"]

    for payload in (
        {"title": "Alert", "body": "BUY ES1! 1"},
        {"symbol": "ES1!", "action": "BUY", "orderType": "MARKET", "quantity": 1},
    ):
        res = client.post(f"/v1/webhooks/{webhook_id}", json=payload)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "skipped"
        assert "outside regular trading hours" in body["reason"]
        assert body.get("alert_id")


def test_structured_futures_payloads_have_distinct_idempotency(client, db_session: Session, monkeypatch):
    user, token = _seed_paid_user(db_session)
    user.api_key_hash = hashlib.sha256(token.encode()).hexdigest()
    user.default_broker = "ninjatrader"
    from app.brokers.ninjatrader import pack_ninjatrader_credentials

    db_session.add(
        BrokerConnection(
            user_id=user.id,
            broker="ninjatrader",
            status="connected",
            encrypted_credentials=encrypt_value(pack_ninjatrader_credentials("", "secret")),
            account_id="Sim101",
        )
    )
    db_session.commit()

    async def fake_get_adapter(db, conn):
        from app.services.crypto import decrypt_value

        return NinjaTraderAdapter.from_credentials(decrypt_value(conn.encrypted_credentials or ""))

    monkeypatch.setattr("app.services.ingest_pipeline.get_adapter", fake_get_adapter)
    monkeypatch.setattr("app.services.market_hours.is_rth", lambda now=None: False)

    create = client.post(
        "/v1/me/webhooks",
        json={"name": "Futures"},
        headers={"Authorization": f"Bearer {token}"},
    )
    webhook_id = create.json()["id"]

    first = client.post(
        f"/v1/webhooks/{webhook_id}",
        json={"symbol": "ES1!", "action": "BUY", "orderType": "MARKET", "quantity": 1},
    )
    second = client.post(
        f"/v1/webhooks/{webhook_id}",
        json={"symbol": "MES1!", "action": "BUY", "orderType": "MARKET", "quantity": 1},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] != "duplicate"


def test_list_brokers_bad_ninjatrader_creds_returns_none_forward_url(client, db_session: Session):
    user, token = _seed_paid_user(db_session)
    user.api_key_hash = hashlib.sha256(token.encode()).hexdigest()
    db_session.add(
        BrokerConnection(
            user_id=user.id,
            broker="ninjatrader",
            status="connected",
            encrypted_credentials="not-valid-fernet",
        )
    )
    db_session.commit()

    list_resp = client.get("/v1/me/brokers", headers={"Authorization": f"Bearer {token}"})
    assert list_resp.status_code == 200
    nt = next(row for row in list_resp.json() if row["broker"] == "ninjatrader")
    assert nt["forward_url"] is None

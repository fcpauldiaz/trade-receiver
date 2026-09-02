import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.brokers.ninjatrader import NinjaTraderAdapter, ninjatrader_futures_symbol
from app.database import Base, get_db
from app.main import app
from app.models.tables import BrokerConnection, Subscription, User
from app.services.crypto import encrypt_value


class FakeNinjaTraderAdapter(NinjaTraderAdapter):
    def __init__(self) -> None:
        super().__init__(forward_url="https://tunnel.example/webhook")

    async def execute_futures_order(self, validated, *, mode: str = "paper", dry_run: bool | None = None):
        from app.brokers.base import OrderResult

        return OrderResult(
            success=True,
            order_id=f"nt-{validated.symbol}",
            fill_price=None,
            raw_response={
                "simulated": dry_run if dry_run is not None else mode == "paper",
                "symbol": validated.symbol,
                "action": validated.action,
            },
        )


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
    import app.api.brokers as brokers_api

    async def _fake_adapter(db, conn):
        return FakeBrokerAdapter()

    brokers_api.get_adapter = _fake_adapter
    import app.services.market_hours as market_hours

    monkeypatch.setattr(market_hours, "is_rth", lambda now=None: True)
    yield TestClient(app), SessionLocal
    app.dependency_overrides.clear()


def _create_user(db, *, active: bool, live: bool = False) -> tuple[User, str]:
    token = secrets.token_urlsafe(32)
    user = User(
        email="paid@example.com",
        api_key_hash=hashlib.sha256(token.encode()).hexdigest(),
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


def test_test_order_blocked_outside_rth(client, monkeypatch):
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

    monkeypatch.setattr("app.services.market_hours.is_rth", lambda now=None: False)

    resp = test_client.post(
        "/v1/me/brokers/tradier/test-order",
        json={"symbol": "SPY", "quantity": 1, "side": "buy"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "regular trading hours" in resp.json()["detail"]


def _connect_ninjatrader(db, user: User) -> None:
    db.add(
        BrokerConnection(
            user_id=user.id,
            broker="ninjatrader",
            status="connected",
            account_id="Sim101",
            encrypted_credentials=encrypt_value('{"forward_url":"https://tunnel.example/webhook"}'),
        )
    )


def test_ninjatrader_dry_run_test_order_allowed_outside_rth(client, monkeypatch):
    test_client, SessionLocal = client
    db = SessionLocal()
    user, token = _create_user(db, active=True)
    _connect_ninjatrader(db, user)
    db.commit()
    db.close()

    import app.api.brokers as brokers_api

    async def _fake_nt_adapter(db, conn):
        return FakeNinjaTraderAdapter()

    brokers_api.get_adapter = _fake_nt_adapter
    monkeypatch.setattr("app.services.market_hours.is_rth", lambda now=None: False)

    resp = test_client.post(
        "/v1/me/brokers/ninjatrader/test-order",
        json={"symbol": "ES", "quantity": 1, "side": "buy", "dry_run": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["broker"] == "ninjatrader"
    assert data["simulated"] is True


def test_ninjatrader_paper_test_order_allowed_outside_rth(client, monkeypatch):
    test_client, SessionLocal = client
    db = SessionLocal()
    user, token = _create_user(db, active=True)
    _connect_ninjatrader(db, user)
    db.commit()
    db.close()

    import app.api.brokers as brokers_api

    async def _fake_nt_adapter(db, conn):
        return FakeNinjaTraderAdapter()

    brokers_api.get_adapter = _fake_nt_adapter
    monkeypatch.setattr("app.services.market_hours.is_rth", lambda now=None: False)

    resp = test_client.post(
        "/v1/me/brokers/ninjatrader/test-order",
        json={"symbol": "ES", "quantity": 1, "side": "buy"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_ninjatrader_live_non_dry_run_blocked_outside_rth(client, monkeypatch):
    test_client, SessionLocal = client
    db = SessionLocal()
    user, token = _create_user(db, active=True, live=True)
    user.default_mode = "live"
    _connect_ninjatrader(db, user)
    db.commit()
    db.close()

    import app.api.brokers as brokers_api

    async def _fake_nt_adapter(db, conn):
        return FakeNinjaTraderAdapter()

    brokers_api.get_adapter = _fake_nt_adapter
    monkeypatch.setattr("app.services.market_hours.is_rth", lambda now=None: False)

    resp = test_client.post(
        "/v1/me/brokers/ninjatrader/test-order",
        json={"symbol": "ES", "quantity": 1, "side": "buy", "dry_run": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "regular trading hours" in resp.json()["detail"]


def test_ninjatrader_test_order_uses_saved_forward_url(client, monkeypatch):
    test_client, SessionLocal = client
    db = SessionLocal()
    user, token = _create_user(db, active=True)
    _connect_ninjatrader(db, user)
    db.commit()
    db.close()

    import app.api.brokers as brokers_api
    from app.services.option_chain import get_adapter

    brokers_api.get_adapter = get_adapter

    captured: dict[str, str] = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"order_id": "nt-123"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(
        "app.brokers.ninjatrader.httpx.AsyncClient",
        lambda *args, **kwargs: FakeClient(),
    )
    monkeypatch.setattr("app.services.market_hours.is_rth", lambda now=None: True)

    resp = test_client.post(
        "/v1/me/brokers/ninjatrader/test-order",
        json={"symbol": "ES", "quantity": 1, "side": "buy", "dry_run": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["broker"] == "ninjatrader"
    assert "forward URL is not configured" not in data["message"]
    assert captured["url"] == "https://tunnel.example/webhook"
    assert captured["json"]["symbol"] == "ES1!"


def test_ninjatrader_futures_symbol_maps_root_continuous():
    assert ninjatrader_futures_symbol("ES") == "ES1!"
    assert ninjatrader_futures_symbol("mes") == "MES1!"
    assert ninjatrader_futures_symbol("NQ") == "NQ1!"
    assert ninjatrader_futures_symbol("MNQ") == "MNQ1!"
    assert ninjatrader_futures_symbol("ES1!") == "ES1!"
    assert ninjatrader_futures_symbol("CL") == "CL"


@pytest.mark.asyncio
async def test_execute_futures_order_includes_forward_reason_on_http_error(monkeypatch):
    class FakeResponse:
        status_code = 400

        @staticmethod
        def json():
            return {
                "status": "rejected",
                "reason": "symbol 'ES' / ntSymbol 'None' is not in AllowedSymbols.",
            }

        text = '{"status":"rejected","reason":"symbol \'ES\' / ntSymbol \'None\' is not in AllowedSymbols."}'

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            return FakeResponse()

    monkeypatch.setattr("app.brokers.ninjatrader.httpx.AsyncClient", lambda timeout: FakeClient())

    from app.schemas.trade import ValidatedFuturesTrade

    adapter = NinjaTraderAdapter(forward_url="https://tunnel.example/webhook")
    validated = ValidatedFuturesTrade(
        action="BUY",
        symbol="ES",
        quantity=1,
        confidence=1.0,
        rationale="test",
        broker="ninjatrader",
        external_id="order-123",
    )
    result = await adapter.execute_futures_order(validated, mode="paper", dry_run=True)
    assert result.success is False
    assert result.error is not None
    assert "forward returned HTTP 400" in result.error
    assert "not in AllowedSymbols" in result.error

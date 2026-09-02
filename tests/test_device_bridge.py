import hashlib
import json
import secrets
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.devices import ws_base_url
from app.brokers.ninjatrader import NinjaTraderAdapter
from app.database import Base, get_db
from app.main import app
from app.models.tables import BrokerConnection, Subscription, User
from app.schemas.trade import ValidatedFuturesTrade
from app.services.crypto import encrypt_value
from app.services.device_bridge import registry
from app.services.device_tokens import pair_device, resolve_device_by_token


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
    monkeypatch.setattr("app.api.devices.SessionLocal", SessionLocal)
    db = SessionLocal()
    yield db, SessionLocal
    db.close()
    app.dependency_overrides.clear()


@pytest.fixture()
def client(db_session):
    return TestClient(app)


def _seed_user(db: Session, *, email: str, active: bool = True) -> tuple[User, str]:
    token = secrets.token_urlsafe(32)
    user = User(
        email=email,
        api_key_hash=hashlib.sha256(token.encode()).hexdigest(),
        default_mode="paper",
    )
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id, status="active" if active else "none", plan_name="pro"))
    db.commit()
    return user, token


def _connect_ninjatrader(db: Session, user: User, forward_url: str = "https://tunnel.example/webhook") -> None:
    db.add(
        BrokerConnection(
            user_id=user.id,
            broker="ninjatrader",
            status="connected",
            encrypted_credentials=encrypt_value(json.dumps({"forward_url": forward_url})),
        )
    )
    db.commit()


def test_pair_device_returns_token_and_ws_url(client, db_session):
    db, _ = db_session
    _, api_token = _seed_user(db, email="pair@example.com")

    resp = client.post(
        "/v1/me/devices/pair",
        json={"name": "NT Workstation"},
        headers={"Authorization": f"Bearer {api_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["device_id"]
    assert data["device_token"].startswith("ntd_")
    assert data["name"] == "NT Workstation"
    assert data["ws_url"] == ws_base_url()
    assert "?token=" not in data["ws_url"]


def test_pair_device_requires_subscription(client, db_session):
    db, _ = db_session
    _, api_token = _seed_user(db, email="unpaid@example.com", active=False)

    resp = client.post(
        "/v1/me/devices/pair",
        json={"name": "NT"},
        headers={"Authorization": f"Bearer {api_token}"},
    )
    assert resp.status_code == 402


def test_list_devices_shows_online_status(client, db_session):
    db, _ = db_session
    user, api_token = _seed_user(db, email="list@example.com")
    device, device_token = pair_device(db, user_id=user.id, name="Desk")

    resp = client.get("/v1/me/devices", headers={"Authorization": f"Bearer {api_token}"})
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["id"] == device.id
    assert rows[0]["online"] is False

    connected = threading.Event()

    def _device_listener():
        with client.websocket_connect(f"/v1/devices/ws?token={device_token}") as ws:
            ws.send_json({"type": "hello", "device_id": device.id})
            connected.set()
            time.sleep(0.5)

    thread = threading.Thread(target=_device_listener, daemon=True)
    thread.start()
    assert connected.wait(timeout=5)
    time.sleep(0.1)

    resp = client.get("/v1/me/devices", headers={"Authorization": f"Bearer {api_token}"})
    assert resp.json()[0]["online"] is True
    thread.join(timeout=2)


def test_websocket_accepts_bearer_auth(client, db_session):
    db, _ = db_session
    user, _ = _seed_user(db, email="bearer@example.com")
    device, device_token = pair_device(db, user_id=user.id, name="Desk")

    with client.websocket_connect(
        "/v1/devices/ws",
        headers={"Authorization": f"Bearer {device_token}"},
    ) as ws:
        ws.send_json({"type": "hello", "device_id": device.id})
        ws.send_json({"type": "pong"})


def test_revoke_device(client, db_session):
    db, _ = db_session
    user, api_token = _seed_user(db, email="revoke@example.com")
    device, device_token = pair_device(db, user_id=user.id, name="Desk")

    delete = client.delete(
        f"/v1/me/devices/{device.id}",
        headers={"Authorization": f"Bearer {api_token}"},
    )
    assert delete.status_code == 200

    assert resolve_device_by_token(db, device_token) is None

    with pytest.raises(Exception):
        with client.websocket_connect(f"/v1/devices/ws?token={device_token}"):
            pass


def test_invalid_device_token_rejected(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/v1/devices/ws?token=ntd_invalid-token"):
            pass


@pytest.mark.asyncio
async def test_push_order_only_reaches_target_user(db_session):
    db, _ = db_session
    user_a, _ = _seed_user(db, email="user-a@example.com")
    user_b, _ = _seed_user(db, email="user-b@example.com")

    from starlette.websockets import WebSocketState
    from unittest.mock import AsyncMock

    ws_a = AsyncMock()
    ws_b = AsyncMock()
    ws_a.client_state = WebSocketState.CONNECTED
    ws_b.client_state = WebSocketState.CONNECTED

    sent_a: list[dict] = []
    sent_b: list[dict] = []
    order_id = "order-abc"

    async def send_a(data):
        sent_a.append(data)
        await registry.handle_incoming(
            user_a.id,
            "device-a",
            {"type": "ack", "id": order_id, "ok": True},
        )

    async def send_b(data):
        sent_b.append(data)

    ws_a.send_json = send_a
    ws_b.send_json = send_b

    await registry.register(user_a.id, "device-a", ws_a)
    await registry.register(user_b.id, "device-b", ws_b)

    payload = {"id": order_id, "symbol": "MES", "action": "BUY", "quantity": 1}
    result = await registry.push_order(user_a.id, payload)

    assert result is not None
    assert result.success is True
    assert len(sent_a) == 1
    assert sent_a[0]["type"] == "order"
    assert sent_a[0]["id"] == order_id
    assert sent_a[0]["payload"]["id"] == order_id
    assert len(sent_b) == 0

    await registry.unregister(user_a.id, "device-a")
    await registry.unregister(user_b.id, "device-b")


@pytest.mark.asyncio
async def test_duplicate_order_id_skipped(db_session):
    db, _ = db_session
    user, _ = _seed_user(db, email="dup@example.com")

    from starlette.websockets import WebSocketState
    from unittest.mock import AsyncMock

    ws = AsyncMock()
    ws.client_state = WebSocketState.CONNECTED
    send_count = 0
    order_id = "dup-order-1"

    async def send_and_ack(data):
        nonlocal send_count
        send_count += 1
        await registry.handle_incoming(
            user.id,
            "device-1",
            {"type": "ack", "id": order_id, "ok": True},
        )

    ws.send_json = send_and_ack
    await registry.register(user.id, "device-1", ws)

    payload = {"id": order_id, "symbol": "MES", "action": "BUY", "quantity": 1}
    first = await registry.push_order(user.id, payload)
    second = await registry.push_order(user.id, payload)

    assert first is not None and first.success
    assert second is not None
    assert second.raw_response.get("status") == "duplicate_skipped"
    assert send_count == 1

    await registry.unregister(user.id, "device-1")


@pytest.mark.asyncio
async def test_ack_ok_false_returns_failure(db_session):
    db, _ = db_session
    user, _ = _seed_user(db, email="reject@example.com")

    from starlette.websockets import WebSocketState
    from unittest.mock import AsyncMock

    ws = AsyncMock()
    ws.client_state = WebSocketState.CONNECTED
    order_id = "reject-order"

    async def send_and_ack(data):
        await registry.handle_incoming(
            user.id,
            "device-1",
            {"type": "ack", "id": order_id, "ok": False, "reason": "symbol not allowed"},
        )

    ws.send_json = send_and_ack
    await registry.register(user.id, "device-1", ws)

    result = await registry.push_order(
        user.id,
        {"id": order_id, "symbol": "MES", "action": "BUY", "quantity": 1},
    )

    assert result is not None
    assert result.success is False
    assert "symbol not allowed" in (result.error or "")

    await registry.unregister(user.id, "device-1")


@pytest.mark.asyncio
async def test_offline_falls_back_to_forward_url(monkeypatch, db_session):
    db, _ = db_session
    user, _ = _seed_user(db, email="fallback@example.com")
    _connect_ninjatrader(db, user)

    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"order_id": "http-123"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("app.brokers.ninjatrader.httpx.AsyncClient", lambda timeout: FakeClient())

    adapter = NinjaTraderAdapter(forward_url="https://tunnel.example/webhook")
    validated = ValidatedFuturesTrade(
        action="BUY",
        symbol="MES",
        quantity=1,
        confidence=1.0,
        rationale="test",
        broker="ninjatrader",
        external_id="offline-order",
    )
    result = await adapter.execute_futures_order(validated, mode="paper", dry_run=True, user_id=user.id)

    assert result.success is True
    assert captured["url"] == "https://tunnel.example/webhook"
    assert captured["json"]["id"] == "offline-order"


@pytest.mark.asyncio
async def test_adapter_uses_device_bridge_when_online(db_session):
    db, _ = db_session
    user, _ = _seed_user(db, email="wss@example.com")
    _connect_ninjatrader(db, user, forward_url="https://tunnel.example/webhook")
    device, _ = pair_device(db, user_id=user.id, name="NT")

    from starlette.websockets import WebSocketState
    from unittest.mock import AsyncMock

    ws = AsyncMock()
    ws.client_state = WebSocketState.CONNECTED
    order_id = "wss-order-1"
    captured: list[dict] = []

    async def send_and_ack(data):
        captured.append(data)
        await registry.handle_incoming(
            user.id,
            device.id,
            {"type": "ack", "id": order_id, "ok": True},
        )

    ws.send_json = send_and_ack
    await registry.register(user.id, device.id, ws)

    adapter = NinjaTraderAdapter(forward_url="https://tunnel.example/webhook")
    validated = ValidatedFuturesTrade(
        action="BUY",
        symbol="MES",
        quantity=1,
        confidence=1.0,
        rationale="test",
        broker="ninjatrader",
        external_id=order_id,
    )
    result = await adapter.execute_futures_order(
        validated, mode="paper", dry_run=True, user_id=user.id
    )

    assert result.success is True
    assert len(captured) == 1
    assert captured[0]["type"] == "order"
    assert captured[0]["id"] == order_id
    assert captured[0]["payload"]["id"] == order_id
    assert result.raw_response.get("transport") == "wss"

    await registry.unregister(user.id, device.id)


def test_list_devices_scoped_to_current_user(client, db_session):
    db, _ = db_session
    user_a, token_a = _seed_user(db, email="scope-a@example.com")
    user_b, token_b = _seed_user(db, email="scope-b@example.com")
    pair_device(db, user_id=user_a.id, name="A Device")
    pair_device(db, user_id=user_b.id, name="B Device")

    resp_a = client.get("/v1/me/devices", headers={"Authorization": f"Bearer {token_a}"})
    resp_b = client.get("/v1/me/devices", headers={"Authorization": f"Bearer {token_b}"})

    assert len(resp_a.json()) == 1
    assert resp_a.json()[0]["name"] == "A Device"
    assert len(resp_b.json()) == 1
    assert resp_b.json()[0]["name"] == "B Device"

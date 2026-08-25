import hashlib
import secrets
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.filter_trade import FilterDecision
from app.database import Base, get_db
from app.main import app
from app.models.tables import InboundAlert, Subscription, User
from tests.test_e2e import FakeAdapter, _seed_paid_user

_ALERT = {"title": "Alert", "body": "BTO SPY 580C 6/20 @ 2.50"}
_ETERMINAL = {
    "type": "signal",
    "firedAt": "2026-07-21T15:00:00.000Z",
    "session": "rth",
    "caution": None,
    "signal": {
        "id": "eterminal-sig-filter",
        "time": 1721596500,
        "price": 6325,
        "shape": "circle",
        "side": "long",
        "variant": "extreme",
        "color": "green",
        "source": "extension-overlay",
    },
    "context": {"currentPrice": 6325},
}


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


def test_settings_roundtrip_trade_filter_prompt(ingest_client, db_session: Session):
    user, token = _seed_paid_user(db_session)
    headers = {"Authorization": f"Bearer {token}"}
    got = ingest_client.get("/v1/me/settings", headers=headers)
    assert got.status_code == 200
    body = got.json()
    assert body["trade_filter_prompt"] is None

    body["trade_filter_prompt"] = "  only puts  "
    put = ingest_client.put("/v1/me/settings", headers=headers, json=body)
    assert put.status_code == 200
    assert put.json()["trade_filter_prompt"] == "only puts"

    again = ingest_client.get("/v1/me/settings", headers=headers)
    assert again.json()["trade_filter_prompt"] == "only puts"

    body["trade_filter_prompt"] = "   "
    cleared = ingest_client.put("/v1/me/settings", headers=headers, json=body)
    assert cleared.status_code == 200
    assert cleared.json()["trade_filter_prompt"] is None


def test_ingest_empty_prompt_does_not_call_openai(ingest_client, db_session: Session, monkeypatch):
    user, token = _seed_paid_user(db_session)
    openai = AsyncMock()
    monkeypatch.setattr("app.agents.filter_trade._filter_with_openai", openai)

    res = ingest_client.post(
        "/v1/ingest",
        json=_ALERT,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    openai.assert_not_called()


def test_ingest_skips_when_filter_rejects(ingest_client, db_session: Session, monkeypatch):
    user, token = _seed_paid_user(db_session)
    user.trade_filter_prompt = "skip calls"
    db_session.commit()
    monkeypatch.setattr("app.agents.filter_trade.settings.openai_api_key", "sk-test")
    monkeypatch.setattr(
        "app.agents.filter_trade._filter_with_openai",
        AsyncMock(return_value=FilterDecision(take=False, reason="calls are banned")),
    )

    res = ingest_client.post(
        "/v1/ingest",
        json=_ALERT,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "skipped"
    assert "calls are banned" in data["reason"]
    stored = db_session.query(InboundAlert).one()
    assert stored.skip_reason and "calls are banned" in stored.skip_reason


def test_ingest_continues_when_filter_takes(ingest_client, db_session: Session, monkeypatch):
    user, token = _seed_paid_user(db_session)
    user.trade_filter_prompt = "only SPY"
    db_session.commit()
    monkeypatch.setattr("app.agents.filter_trade.settings.openai_api_key", "sk-test")
    monkeypatch.setattr(
        "app.agents.filter_trade._filter_with_openai",
        AsyncMock(return_value=FilterDecision(take=True, reason="ok")),
    )

    res = ingest_client.post(
        "/v1/ingest",
        json=_ALERT,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["status"] in {"filled", "validation_failed"}


def test_ingest_fail_closed_when_prompt_set_without_key(ingest_client, db_session: Session, monkeypatch):
    user, token = _seed_paid_user(db_session)
    user.trade_filter_prompt = "only puts"
    db_session.commit()
    monkeypatch.setattr("app.agents.filter_trade.settings.openai_api_key", None)

    res = ingest_client.post(
        "/v1/ingest",
        json=_ALERT,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "skipped"
    assert "filter unavailable" in data["reason"]


def test_eterminal_does_not_call_trade_filter(ingest_client, db_session: Session, monkeypatch):
    user, token = _seed_paid_user(db_session)
    user.trade_filter_prompt = "skip everything"
    db_session.commit()
    filter_fn = AsyncMock()
    monkeypatch.setattr("app.api.ingest.apply_trade_filter", filter_fn)

    res = ingest_client.post(
        "/v1/ingest",
        json=_ETERMINAL,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "filled"
    filter_fn.assert_not_called()


def test_concurrent_ingest_same_alert_serializes_without_server_error(
    ingest_client, db_session: Session
):
    import asyncio

    import httpx
    from httpx import ASGITransport

    from app.main import app

    user, token = _seed_paid_user(db_session)
    user.api_key_hash = hashlib.sha256(token.encode()).hexdigest()
    db_session.commit()

    payload = {"title": "Alerts", "body": "BTO SPY 580C 6/20 @ 2.50", "app_id": "com.discord"}
    headers = {"Authorization": f"Bearer {token}"}

    async def run_concurrent_posts() -> list[int]:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            tasks = [client.post("/v1/ingest", json=payload, headers=headers) for _ in range(8)]
            responses = await asyncio.gather(*tasks)
            return [response.status_code for response in responses]

    status_codes = asyncio.run(run_concurrent_posts())

    assert all(code == 200 for code in status_codes)
    alerts = db_session.query(InboundAlert).filter_by(user_id=user.id).all()
    assert len(alerts) == 1


def test_ingest_integrity_error_returns_duplicate(ingest_client, db_session: Session, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from app.models.tables import InboundAlert
    from app.services.webhook_normalize import idempotency_key, normalize_webhook_body

    user, token = _seed_paid_user(db_session)
    user.api_key_hash = hashlib.sha256(token.encode()).hexdigest()
    db_session.commit()

    payload = {"title": "Alerts", "body": "BTO SPY 580C 6/20 @ 2.50", "app_id": "com.discord"}
    _, webhook_payload = normalize_webhook_body(payload)
    key = idempotency_key(user.id, webhook_payload)
    existing = InboundAlert(
        user_id=user.id,
        idempotency_key=key,
        raw_payload="{}",
        normalized_text="cached",
    )
    db_session.add(existing)
    db_session.commit()

    async def raise_integrity(_db, _user, _body):
        raise IntegrityError("insert", {}, Exception("unique constraint"))

    monkeypatch.setattr("app.api.ingest._process_inbound_alert", raise_integrity)

    res = ingest_client.post(
        "/v1/ingest",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json() == {"status": "duplicate", "alert_id": existing.id}

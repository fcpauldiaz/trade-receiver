import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.tables import Subscription, User
from app.services.jwt_auth import hash_api_key

INTERNAL_SECRET = "test-internal-secret"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    from app.config import settings

    settings.internal_api_secret = INTERNAL_SECRET

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


def test_provision_creates_user(client):
    http, _ = client
    res = http.post(
        "/v1/internal/provision",
        json={"auth_id": "auth-1", "email": "new@example.com", "name": "New"},
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["created"] is True
    assert body["linked"] is False


def test_provision_links_legacy_email(client):
    http, db_factory = client
    db = db_factory()
    user = User(email="legacy@example.com", api_key_hash=hash_api_key("legacy-key"))
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id, status="none", plan_name="free"))
    db.commit()
    user_id = user.id
    db.close()

    res = http.post(
        "/v1/internal/provision",
        json={"auth_id": "auth-legacy", "email": "legacy@example.com", "name": "Legacy"},
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["linked"] is True
    assert body["user_id"] == user_id


def test_provision_requires_secret(client):
    http, _ = client
    res = http.post(
        "/v1/internal/provision",
        json={"auth_id": "auth-2", "email": "x@example.com"},
        headers={"X-Internal-Secret": "wrong"},
    )
    assert res.status_code == 401


def test_api_key_still_works(client):
    http, db_factory = client
    db = db_factory()
    token = secrets.token_urlsafe(32)
    user = User(
        email="bearer@example.com",
        better_auth_id="auth-bearer",
        api_key_hash=hashlib.sha256(token.encode()).hexdigest(),
    )
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id, status="none", plan_name="free"))
    db.commit()
    db.close()

    res = http.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["email"] == "bearer@example.com"

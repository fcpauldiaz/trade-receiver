import hashlib
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.tables import Review, Subscription, User


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


def _create_user(db, *, email: str, active: bool) -> tuple[User, str]:
    token = secrets.token_urlsafe(32)
    user = User(
        email=email,
        name="Test User",
        api_key_hash=hashlib.sha256(token.encode()).hexdigest(),
    )
    db.add(user)
    db.flush()
    status = "active" if active else "none"
    db.add(Subscription(user_id=user.id, status=status, plan_name="pro" if active else "free"))
    db.commit()
    return user, token


def test_list_reviews_public(client):
    test_client, SessionLocal = client
    db = SessionLocal()
    user, _ = _create_user(db, email="a@example.com", active=True)
    db.add(
        Review(
            user_id=user.id,
            rating=5,
            body="Great product",
            author_name="Test User",
        )
    )
    db.commit()
    db.close()

    resp = test_client.get("/v1/reviews")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["rating"] == 5
    assert data[0]["verified_customer"] is True
    assert "user_id" not in data[0]


def test_post_requires_auth(client):
    test_client, _ = client
    resp = test_client.post("/v1/me/reviews", json={"rating": 5, "body": "Nice"})
    assert resp.status_code == 401


def test_post_requires_active_subscription(client):
    test_client, SessionLocal = client
    db = SessionLocal()
    _, token = _create_user(db, email="free@example.com", active=False)
    db.close()

    resp = test_client.post(
        "/v1/me/reviews",
        json={"rating": 4, "body": "Good"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 402


def test_active_subscriber_can_create_and_update(client):
    test_client, SessionLocal = client
    db = SessionLocal()
    _, token = _create_user(db, email="paid@example.com", active=True)
    db.close()

    headers = {"Authorization": f"Bearer {token}"}
    create = test_client.post(
        "/v1/me/reviews",
        json={"rating": 5, "body": "Excellent"},
        headers=headers,
    )
    assert create.status_code == 200
    assert create.json()["body"] == "Excellent"

    update = test_client.post(
        "/v1/me/reviews",
        json={"rating": 4, "body": "Updated review"},
        headers=headers,
    )
    assert update.status_code == 200
    assert update.json()["body"] == "Updated review"

    mine = test_client.get("/v1/me/review", headers=headers)
    assert mine.status_code == 200
    assert mine.json()["rating"] == 4

    listed = test_client.get("/v1/reviews")
    assert len(listed.json()) == 1


def test_invalid_rating_rejected(client):
    test_client, SessionLocal = client
    db = SessionLocal()
    _, token = _create_user(db, email="paid2@example.com", active=True)
    db.close()

    resp = test_client.post(
        "/v1/me/reviews",
        json={"rating": 6, "body": "Too high"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_delete_review(client):
    test_client, SessionLocal = client
    db = SessionLocal()
    _, token = _create_user(db, email="paid3@example.com", active=True)
    db.close()
    headers = {"Authorization": f"Bearer {token}"}
    test_client.post("/v1/me/reviews", json={"rating": 3, "body": "Ok"}, headers=headers)
    resp = test_client.delete("/v1/me/reviews", headers=headers)
    assert resp.status_code == 200
    assert test_client.get("/v1/me/review", headers=headers).json() is None

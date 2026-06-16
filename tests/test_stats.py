import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.tables import User


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
    db = SessionLocal()
    db.add(User(email="a@example.com"))
    db.add(User(email="b@example.com"))
    db.add(User(email="c@example.com"))
    db.commit()
    db.close()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_public_stats_user_count(client: TestClient):
    res = client.get("/v1/stats/public")
    assert res.status_code == 200
    assert res.json()["user_count"] == 3

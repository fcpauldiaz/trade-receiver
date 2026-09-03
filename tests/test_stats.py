import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.tables import User


@pytest.fixture()
def client(db_factory):
    db = db_factory()
    db.add(User(email="a@example.com"))
    db.add(User(email="b@example.com"))
    db.add(User(email="c@example.com"))
    db.commit()
    db.close()
    return TestClient(app)


def test_public_stats_user_count(client: TestClient):
    res = client.get("/v1/stats/public")
    assert res.status_code == 200
    assert res.json()["user_count"] == 3

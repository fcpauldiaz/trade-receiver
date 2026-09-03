from app.models.tables import Subscription
from tests.db_helpers import insert_better_auth_user


def test_grant_subscription_activates_pro(client, monkeypatch):
    http, db_factory = client
    monkeypatch.setenv("INTERNAL_API_SECRET", "test-internal")
    from app.config import settings

    monkeypatch.setattr(settings, "internal_api_secret", "test-internal")

    db = db_factory()
    insert_better_auth_user(db, user_id="u1", email="owner@example.com", name="Owner")
    db.add(Subscription(user_id="u1", status="none", plan_name="free"))
    db.commit()
    db.close()

    response = http.post(
        "/v1/internal/subscription/grant",
        headers={"X-Internal-Secret": "test-internal"},
        json={"email": "owner@example.com", "status": "active", "plan_name": "pro"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "active"
    assert body["plan_name"] == "pro"
    assert body["can_process_trades"] is True

    db = db_factory()
    sub = db.query(Subscription).filter_by(user_id="u1").one()
    assert sub.status == "active"
    assert sub.plan_name == "pro"
    db.close()

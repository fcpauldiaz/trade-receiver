from decimal import Decimal

import pytest

from app.config import settings
from app.models.tables import AiEvaluation, Subscription, User
from app.services import jwt_auth
from tests.db_helpers import insert_better_auth_user
from tests.jwks_helpers import AUTH_EMAIL, AUTH_USER_ID, blocked_urllib_jwks_issuer


def _reset_jwks_client() -> None:
    jwt_auth._jwks_client = None
    jwt_auth._jwks_url = None


@pytest.fixture()
def admin_api(client, monkeypatch):
    with blocked_urllib_jwks_issuer(user_id=AUTH_USER_ID, email=AUTH_EMAIL) as issued:
        monkeypatch.setattr(settings, "better_auth_url", issued.issuer)
        _reset_jwks_client()
        http, db_factory = client
        db = db_factory()
        insert_better_auth_user(db, user_id=AUTH_USER_ID, email=AUTH_EMAIL, name="Admin")
        user = db.get(User, AUTH_USER_ID)
        assert user is not None
        user.role = "admin"
        db.add(Subscription(user_id=AUTH_USER_ID, status="active", plan_name="pro"))
        db.add(
            AiEvaluation(
                user_id=AUTH_USER_ID,
                kind="filter",
                decision="take",
                rationale="ok",
                model="openai/gpt-4o-mini",
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                cost_usd=Decimal("0.000010"),
                latency_ms=20,
            )
        )
        db.commit()
        db.close()
        try:
            yield http, {"Authorization": f"Bearer {issued.token}"}
        finally:
            _reset_jwks_client()


@pytest.fixture()
def non_admin_api(client, monkeypatch):
    with blocked_urllib_jwks_issuer(user_id=AUTH_USER_ID, email=AUTH_EMAIL) as issued:
        monkeypatch.setattr(settings, "better_auth_url", issued.issuer)
        _reset_jwks_client()
        http, db_factory = client
        db = db_factory()
        insert_better_auth_user(db, user_id=AUTH_USER_ID, email=AUTH_EMAIL, name="User")
        db.add(Subscription(user_id=AUTH_USER_ID, status="none", plan_name="free"))
        db.commit()
        db.close()
        try:
            yield http, {"Authorization": f"Bearer {issued.token}"}
        finally:
            _reset_jwks_client()


def test_admin_overview_ok(admin_api):
    http, headers = admin_api
    resp = http.get("/v1/admin/overview", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_count"] >= 1
    assert body["ai_calls_today"] >= 1
    assert body["cost_usd_today"] >= 0
    assert len(body["latest_evaluations"]) >= 1


def test_admin_ai_evaluations_ok(admin_api):
    http, headers = admin_api
    resp = http.get("/v1/admin/ai-evaluations", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    assert body["items"][0]["kind"] == "filter"
    assert body["items"][0]["total_tokens"] == 15


def test_admin_users_and_grant(admin_api):
    http, headers = admin_api
    users = http.get("/v1/admin/users", headers=headers)
    assert users.status_code == 200, users.text
    assert users.json()[0]["role"] == "admin"
    user_id = users.json()[0]["id"]
    grant = http.post(
        f"/v1/admin/users/{user_id}/subscription",
        headers=headers,
        json={"status": "active", "plan_name": "pro"},
    )
    assert grant.status_code == 200, grant.text
    assert grant.json()["can_process_trades"] is True


def test_me_includes_role(admin_api):
    http, headers = admin_api
    resp = http.get("/v1/me", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"


def test_non_admin_forbidden(non_admin_api):
    http, headers = non_admin_api
    resp = http.get("/v1/admin/overview", headers=headers)
    assert resp.status_code == 403
    me = http.get("/v1/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["role"] == "user"

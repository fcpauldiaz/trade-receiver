import pytest
from fastapi.testclient import TestClient
from jwt import PyJWKClient, PyJWTError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.tables import Subscription
from app.services import jwt_auth
from tests.db_helpers import insert_better_auth_user
from tests.jwks_helpers import AUTH_EMAIL, AUTH_USER_ID, blocked_urllib_jwks_issuer


def _reset_jwks_client() -> None:
    jwt_auth._jwks_client = None
    jwt_auth._jwks_url = None


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


@pytest.fixture()
def jwks_issuer(monkeypatch):
    with blocked_urllib_jwks_issuer() as issued:
        monkeypatch.setattr(settings, "better_auth_url", issued.issuer)
        _reset_jwks_client()
        try:
            yield issued
        finally:
            _reset_jwks_client()


@pytest.fixture()
def jwt_api(client, jwks_issuer):
    http, db_factory = client
    db = db_factory()
    insert_better_auth_user(db, user_id=AUTH_USER_ID, email=AUTH_EMAIL, name="User")
    db.add(Subscription(user_id=AUTH_USER_ID, status="none", plan_name="free"))
    db.commit()
    db.close()
    return http, {"Authorization": f"Bearer {jwks_issuer.token}"}


def test_jwks_user_agent_is_not_python_urllib():
    ua = jwt_auth._JWKS_HEADERS["User-Agent"]
    assert "Python-urllib" not in ua
    assert ua


def test_default_urllib_jwks_client_cannot_fetch_blocked_keys(jwks_issuer):
    client = PyJWKClient(f"{jwks_issuer.issuer}/api/auth/jwks", cache_keys=True, lifespan=3600)
    with pytest.raises(PyJWTError):
        client.get_signing_key_from_jwt(jwks_issuer.token)


def test_verify_jwt_when_default_urllib_user_agent_is_blocked(jwks_issuer):
    claims = jwt_auth.verify_better_auth_jwt(jwks_issuer.token)
    assert claims.sub == AUTH_USER_ID
    assert claims.email == AUTH_EMAIL


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/me"),
        ("GET", "/v1/me/billing"),
        ("GET", "/v1/me/brokers"),
        ("GET", "/v1/me/settings"),
    ],
)
def test_jwt_dashboard_routes_when_cloudflare_blocks_urllib_jwks(jwt_api, method, path):
    http, headers = jwt_api
    response = http.request(method, path, headers=headers)
    assert response.status_code != 401, response.text
    assert response.status_code != 500, response.text


def test_jwt_billing_and_brokers_load_for_free_user(jwt_api):
    http, headers = jwt_api
    billing = http.get("/v1/me/billing", headers=headers)
    assert billing.status_code == 200
    body = billing.json()
    assert body["status"] == "none"
    assert body["can_process_trades"] is False

    brokers = http.get("/v1/me/brokers", headers=headers)
    assert brokers.status_code == 200
    assert brokers.json() == []

    settings_res = http.get("/v1/me/settings", headers=headers)
    assert settings_res.status_code == 200


def test_jwt_unknown_user_is_not_provisioned(client, jwks_issuer):
    http, _ = client
    response = http.get(
        "/v1/me",
        headers={"Authorization": f"Bearer {jwks_issuer.token}"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "User not provisioned"

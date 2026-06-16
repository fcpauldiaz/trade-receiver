import hashlib
import secrets
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

from app.config import settings

_jwks_client: PyJWKClient | None = None
_jwks_url: str | None = None


@dataclass(frozen=True)
class JwtClaims:
    sub: str
    email: str | None


def _jwks_endpoint() -> str:
    base = settings.better_auth_url.rstrip("/")
    return f"{base}/api/auth/jwks"


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client, _jwks_url
    url = _jwks_endpoint()
    if _jwks_client is None or _jwks_url != url:
        _jwks_client = PyJWKClient(url, cache_keys=True, lifespan=3600)
        _jwks_url = url
    return _jwks_client


def verify_better_auth_jwt(token: str) -> JwtClaims:
    client = _get_jwks_client()
    signing_key = client.get_signing_key_from_jwt(token)
    issuer = settings.better_auth_url.rstrip("/")
    payload = jwt.decode(
        token,
        signing_key.key,
        algorithms=["EdDSA", "ES256", "RS256"],
        issuer=issuer,
        audience=issuer,
        options={"require": ["sub", "exp", "iss", "aud"]},
    )
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise jwt.InvalidTokenError("Missing sub claim")
    email = payload.get("email")
    return JwtClaims(sub=sub, email=email if isinstance(email, str) else None)


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)

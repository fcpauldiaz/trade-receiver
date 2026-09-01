import hmac
import secrets

from app.services.jwt_auth import hash_api_key


def generate_webhook_secret() -> str:
    return secrets.token_urlsafe(32)


def verify_webhook_secret(provided: str, secret_hash: str) -> bool:
    if not provided or not secret_hash:
        return False
    expected = hash_api_key(provided)
    return hmac.compare_digest(expected, secret_hash)

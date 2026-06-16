import base64
import hashlib
import hmac
import json
import secrets
import time

from app.config import settings

_STATE_TTL_SECONDS = 600


def create_oauth_state(user_id: str, broker: str) -> str:
    payload = {
        "user_id": user_id,
        "broker": broker,
        "exp": int(time.time()) + _STATE_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(8),
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(
        settings.api_secret_key.encode(),
        body.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{body}.{sig}"


def verify_oauth_state(state: str, broker: str) -> str:
    try:
        body, sig = state.rsplit(".", 1)
    except ValueError as exc:
        raise ValueError("Invalid OAuth state") from exc

    expected = hmac.new(
        settings.api_secret_key.encode(),
        body.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ValueError("Invalid OAuth state signature")

    payload = json.loads(base64.urlsafe_b64decode(body.encode()))
    if payload.get("broker") != broker:
        raise ValueError("OAuth state broker mismatch")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("OAuth state expired")

    user_id = payload.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        raise ValueError("OAuth state missing user_id")
    return user_id


def oauth_success_redirect(broker: str) -> str:
    base = settings.platform_base_url.rstrip("/")
    return f"{base}/onboarding?broker={broker}"

import os

from app.config import settings

INSECURE_DEFAULTS = {
    "change-me-in-production",
    "change-me-32-byte-key-for-tokens!!",
}


def validate_production_settings() -> None:
    if os.getenv("ENVIRONMENT", "development") != "production":
        return
    if settings.api_secret_key in INSECURE_DEFAULTS:
        raise RuntimeError("API_SECRET_KEY must be set in production")
    if settings.encryption_key in INSECURE_DEFAULTS:
        raise RuntimeError("ENCRYPTION_KEY must be set in production")
    if not settings.lemon_squeezy_webhook_secret:
        raise RuntimeError("LEMON_SQUEEZY_WEBHOOK_SECRET must be set in production")
    if not settings.internal_api_secret or settings.internal_api_secret == "dev-internal-secret":
        raise RuntimeError("INTERNAL_API_SECRET must be set in production")
    if not settings.better_auth_url or settings.better_auth_url.startswith("http://localhost"):
        raise RuntimeError("BETTER_AUTH_URL must be set to the public platform URL in production")
    if "libsql" in settings.database_url and not settings.turso_auth_token:
        raise RuntimeError("TURSO_AUTH_TOKEN must be set when DATABASE_URL uses Turso/libsql")

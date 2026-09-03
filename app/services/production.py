import os
from urllib.parse import urlparse

from app.config import settings

INSECURE_DEFAULTS = {
    "change-me-in-production",
    "change-me-32-byte-key-for-tokens!!",
}

_POSTGRES_SCHEMES = {"postgres", "postgresql", "postgresql+psycopg"}


def validate_production_settings() -> None:
    if os.getenv("ENVIRONMENT", "development") != "production":
        return
    if settings.api_secret_key in INSECURE_DEFAULTS:
        raise RuntimeError("API_SECRET_KEY must be set in production")
    if settings.encryption_key in INSECURE_DEFAULTS:
        raise RuntimeError("ENCRYPTION_KEY must be set in production")
    if not settings.internal_api_secret or settings.internal_api_secret == "dev-internal-secret":
        raise RuntimeError("INTERNAL_API_SECRET must be set in production")
    if not settings.better_auth_url or settings.better_auth_url.startswith("http://localhost"):
        raise RuntimeError("BETTER_AUTH_URL must be set to the public platform URL in production")
    scheme = urlparse(settings.database_url).scheme
    if scheme not in _POSTGRES_SCHEMES:
        raise RuntimeError("DATABASE_URL must be a PostgreSQL URL in production")

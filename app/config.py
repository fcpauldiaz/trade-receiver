from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+libsql:///./data/trade.db"
    turso_auth_token: str | None = None
    api_secret_key: str = "change-me-in-production"
    encryption_key: str = "change-me-32-byte-key-for-tokens!!"

    openai_api_key: str | None = None
    ai_model: str = "gpt-4o-mini"
    ai_confidence_threshold: float = 0.8

    lemon_squeezy_webhook_secret: str | None = None
    lemon_squeezy_api_key: str | None = None

    receiver_base_url: str = "http://localhost:8000"
    platform_base_url: str = "http://localhost:3000"

    tradier_api_base: str = "https://sandbox.tradier.com/v1"
    tradier_client_id: str | None = None
    tradier_client_secret: str | None = None
    tradier_redirect_uri: str = "http://localhost:8000/v1/me/brokers/tradier/callback"
    tradier_oauth_scope: str = "read write trade market"

    schwab_client_id: str | None = None
    schwab_client_secret: str | None = None
    schwab_redirect_uri: str = "http://localhost:8000/v1/me/brokers/schwab/callback"

    webull_enabled: bool = False


settings = Settings()

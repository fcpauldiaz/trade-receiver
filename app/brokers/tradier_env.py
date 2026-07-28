from typing import Literal

from app.config import settings

TradierEnvironment = Literal["sandbox", "live"]

TRADIER_API_BASES: dict[TradierEnvironment, str] = {
    "sandbox": "https://sandbox.tradier.com/v1",
    "live": "https://api.tradier.com/v1",
}


def normalize_tradier_environment(value: str | None) -> TradierEnvironment:
    if value in ("sandbox", "live"):
        return value
    base = (settings.tradier_api_base or "").lower()
    if "sandbox" in base:
        return "sandbox"
    return "live"


def tradier_api_base(environment: str | None) -> str:
    env = normalize_tradier_environment(environment)
    return TRADIER_API_BASES[env]

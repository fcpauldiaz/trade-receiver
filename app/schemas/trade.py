from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class WebhookPayload(BaseModel):
    app_id: str = ""
    title: str = ""
    subtitle: str = ""
    body: str = ""
    delivered_date: float | None = None
    delivered_date_iso: str = ""
    platform: str = ""


class DiscordEmbed(BaseModel):
    title: str | None = None
    description: str | None = None
    footer: dict | None = None
    timestamp: str | None = None


class DiscordWebhookPayload(BaseModel):
    embeds: list[DiscordEmbed] = Field(default_factory=list)


class TradeIntent(BaseModel):
    action: Literal["buy_to_open", "sell_to_close", "skip"] = "skip"
    underlying: str = ""
    option_type: Literal["call", "put"] = "call"
    strike: Decimal = Decimal("0")
    expiration: date | None = None
    quantity: int = 1
    order_type: Literal["market", "limit"] = "market"
    limit_price: Decimal | None = None
    confidence: float = 0.0
    rationale: str = ""


class ValidatedTrade(BaseModel):
    action: Literal["buy_to_open", "sell_to_close", "skip"]
    underlying: str
    option_type: Literal["call", "put"]
    strike: Decimal
    expiration: date
    quantity: int
    order_type: Literal["market", "limit"]
    limit_price: Decimal | None
    confidence: float
    rationale: str
    broker: Literal["schwab", "tradier", "webull"]
    contract_symbol: str
    bid: Decimal | None = None
    ask: Decimal | None = None
    open_interest: int | None = None
    validation_errors: list[str] = Field(default_factory=list)

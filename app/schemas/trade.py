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


AssetClass = Literal["option", "future"]
BrokerName = Literal["schwab", "tradier", "webull", "ninjatrader"]
FuturesAction = Literal["BUY", "SELL"]


class TradeIntent(BaseModel):
    action: Literal["buy_to_open", "sell_to_close", "skip"] = "skip"
    asset_class: AssetClass = "option"
    underlying: str = ""
    option_type: Literal["call", "put"] = "call"
    strike: Decimal = Decimal("0")
    expiration: date | None = None
    quantity: int = 1
    order_type: Literal["market", "limit"] = "market"
    limit_price: Decimal | None = None
    confidence: float = 0.0
    rationale: str = ""
    take_profit_pct: Decimal | None = None
    source: str = ""
    notional_usd: Decimal | None = None
    stop_loss_ticks: int | None = None
    profit_target_ticks: int | None = None
    external_id: str = ""


class ValidatedTrade(BaseModel):
    action: Literal["buy_to_open", "sell_to_close", "skip"]
    asset_class: AssetClass = "option"
    underlying: str
    option_type: Literal["call", "put"]
    strike: Decimal
    expiration: date
    quantity: int
    order_type: Literal["market", "limit"]
    limit_price: Decimal | None
    confidence: float
    rationale: str
    broker: BrokerName
    contract_symbol: str
    bid: Decimal | None = None
    ask: Decimal | None = None
    open_interest: int | None = None
    validation_errors: list[str] = Field(default_factory=list)
    take_profit_pct: Decimal | None = None
    source: str = ""
    notional_usd: Decimal | None = None


class ValidatedFuturesTrade(BaseModel):
    asset_class: Literal["future"] = "future"
    action: FuturesAction
    symbol: str
    quantity: int
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    stop_loss_ticks: int | None = None
    profit_target_ticks: int | None = None
    confidence: float
    rationale: str
    broker: Literal["ninjatrader"]
    external_id: str = ""
    validation_errors: list[str] = Field(default_factory=list)


class NinjaTraderOrderPayload(BaseModel):
    id: str
    symbol: str
    action: FuturesAction
    orderType: Literal["MARKET", "LIMIT"] = "MARKET"
    quantity: int
    stopLossTicks: int | None = None
    profitTargetTicks: int | None = None
    dryRun: bool | None = None

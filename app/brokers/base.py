from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol

BrokerName = Literal["schwab", "tradier", "webull"]
TradeMode = Literal["paper", "live"]


@dataclass
class OptionContract:
    symbol: str
    underlying: str
    option_type: str
    strike: Decimal
    expiration: date
    bid: Decimal | None
    ask: Decimal | None
    open_interest: int | None


@dataclass
class OrderResult:
    success: bool
    order_id: str | None
    fill_price: Decimal | None
    raw_response: dict
    error: str | None = None


class BrokerAdapter(Protocol):
    name: BrokerName

    async def get_option_chain(
        self, underlying: str, expiration: date | None = None
    ) -> list[OptionContract]: ...

    async def preview_order(self, contract: OptionContract, quantity: int, side: str) -> OrderResult: ...

    async def place_order(
        self, contract: OptionContract, quantity: int, side: str, mode: TradeMode
    ) -> OrderResult: ...

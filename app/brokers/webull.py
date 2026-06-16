from datetime import date
from decimal import Decimal

from app.brokers.base import OptionContract, OrderResult
from app.config import settings


class WebullAdapter:
    name = "webull"

    def __init__(self, access_token: str | None = None):
        self.access_token = access_token or ""

    async def get_option_chain(self, underlying: str, expiration: date | None = None) -> list[OptionContract]:
        if not settings.webull_enabled:
            return []
        return []

    async def preview_order(self, contract: OptionContract, quantity: int, side: str) -> OrderResult:
        return OrderResult(success=False, order_id=None, fill_price=None, raw_response={}, error="Webull not enabled")

    async def place_order(self, contract: OptionContract, quantity: int, side: str, mode: str) -> OrderResult:
        return OrderResult(success=False, order_id=None, fill_price=None, raw_response={}, error="Webull not enabled")

    async def get_account_equity(self) -> Decimal | None:
        if not settings.webull_enabled:
            return None
        return Decimal("100000")

    async def place_equity_order(self, symbol: str, quantity: int, side: str, mode: str) -> OrderResult:
        if mode == "paper" and settings.webull_enabled:
            return OrderResult(
                success=True,
                order_id=f"paper-webull-{symbol}",
                fill_price=None,
                raw_response={"simulated": True, "symbol": symbol, "quantity": quantity},
            )
        return OrderResult(success=False, order_id=None, fill_price=None, raw_response={}, error="Webull not enabled")

    async def get_positions(self) -> list[dict]:
        return []

    async def get_order_status(self, order_id: str) -> dict | None:
        return None

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

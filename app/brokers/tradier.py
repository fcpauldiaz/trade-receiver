from datetime import date
from decimal import Decimal

import httpx

from app.brokers.base import BrokerAdapter, OptionContract, OrderResult
from app.config import settings


class TradierAdapter:
    name = "tradier"

    def __init__(self, access_token: str | None = None, account_id: str | None = None):
        self.access_token = access_token or settings.tradier_access_token or ""
        self.account_id = account_id or settings.tradier_account_id or ""
        self.base = settings.tradier_api_base

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    async def get_option_chain(
        self, underlying: str, expiration: date | None = None
    ) -> list[OptionContract]:
        if not self.access_token:
            return []
        params: dict[str, str] = {"symbol": underlying.upper()}
        if expiration:
            params["expiration"] = expiration.isoformat()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base}/markets/options/chains",
                headers=self._headers(),
                params=params,
            )
            if resp.status_code >= 400:
                return []
            data = resp.json()
        contracts: list[OptionContract] = []
        for opt in data.get("options", {}).get("option", []) or []:
            contracts.append(
                OptionContract(
                    symbol=opt.get("symbol", ""),
                    underlying=underlying.upper(),
                    option_type="call" if opt.get("option_type") == "call" else "put",
                    strike=Decimal(str(opt.get("strike", 0))),
                    expiration=date.fromisoformat(opt.get("expiration_date", "1970-01-01")),
                    bid=Decimal(str(opt["bid"])) if opt.get("bid") is not None else None,
                    ask=Decimal(str(opt["ask"])) if opt.get("ask") is not None else None,
                    open_interest=int(opt["open_interest"]) if opt.get("open_interest") else None,
                )
            )
        return contracts

    async def preview_order(self, contract: OptionContract, quantity: int, side: str) -> OrderResult:
        return OrderResult(success=True, order_id=None, fill_price=contract.ask, raw_response={"preview": True})

    async def place_order(
        self, contract: OptionContract, quantity: int, side: str, mode: str
    ) -> OrderResult:
        if not self.access_token or not self.account_id:
            return OrderResult(success=False, order_id=None, fill_price=None, raw_response={}, error="not configured")
        if mode == "paper" and "sandbox" not in self.base:
            return OrderResult(
                success=True,
                order_id=f"paper-{contract.symbol}",
                fill_price=contract.ask,
                raw_response={"simulated": True, "mode": "paper"},
            )
        data = {
            "class": "option",
            "symbol": contract.underlying,
            "option_symbol": contract.symbol,
            "side": side,
            "quantity": str(quantity),
            "type": "market",
            "duration": "day",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base}/accounts/{self.account_id}/orders",
                headers=self._headers(),
                data=data,
            )
            body = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                return OrderResult(
                    success=False,
                    order_id=None,
                    fill_price=None,
                    raw_response=body,
                    error=str(body),
                )
            order_id = str(body.get("order", {}).get("id", ""))
            return OrderResult(success=True, order_id=order_id, fill_price=contract.ask, raw_response=body)

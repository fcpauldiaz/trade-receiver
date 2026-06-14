from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from app.brokers.base import OptionContract, OrderResult
from app.config import settings


class SchwabAdapter:
    name = "schwab"

    def __init__(self, access_token: str | None = None):
        self.access_token = access_token or ""

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}

    @staticmethod
    def authorization_url(state: str) -> str:
        params = urlencode({
            "client_id": settings.schwab_client_id or "",
            "redirect_uri": settings.schwab_redirect_uri,
            "response_type": "code",
            "state": state,
        })
        return f"https://api.schwabapi.com/v1/oauth/authorize?{params}"

    async def exchange_code(self, code: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.schwabapi.com/v1/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.schwab_redirect_uri,
                    "client_id": settings.schwab_client_id,
                    "client_secret": settings.schwab_client_secret,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def get_option_chain(self, underlying: str, expiration: date | None = None) -> list[OptionContract]:
        if not self.access_token:
            return []
        params: dict[str, str] = {"symbol": underlying.upper()}
        if expiration:
            params["fromDate"] = expiration.isoformat()
            params["toDate"] = expiration.isoformat()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://api.schwabapi.com/marketdata/v1/chains",
                headers=self._headers(),
                params=params,
            )
            if resp.status_code >= 400:
                return []
            data = resp.json()
        contracts: list[OptionContract] = []
        for side_key, side_name in (("callExpDateMap", "call"), ("putExpDateMap", "put")):
            for _exp, strikes in (data.get(side_key) or {}).items():
                for strike_key, legs in strikes.items():
                    for leg in legs:
                        contracts.append(
                            OptionContract(
                                symbol=leg.get("symbol", ""),
                                underlying=underlying.upper(),
                                option_type=side_name,
                                strike=Decimal(str(leg.get("strikePrice", strike_key))),
                                expiration=date.fromisoformat(leg.get("expirationDate", "1970-01-01")[:10]),
                                bid=Decimal(str(leg["bid"])) if leg.get("bid") is not None else None,
                                ask=Decimal(str(leg["ask"])) if leg.get("ask") is not None else None,
                                open_interest=int(leg.get("openInterest", 0)) or None,
                            )
                        )
        return contracts

    async def preview_order(self, contract: OptionContract, quantity: int, side: str) -> OrderResult:
        return OrderResult(success=True, order_id=None, fill_price=contract.ask, raw_response={"preview": True})

    async def place_order(self, contract: OptionContract, quantity: int, side: str, mode: str) -> OrderResult:
        if mode == "paper":
            return OrderResult(
                success=True,
                order_id=f"paper-schwab-{contract.symbol}",
                fill_price=contract.ask,
                raw_response={"simulated": True},
            )
        return OrderResult(
            success=False,
            order_id=None,
            fill_price=None,
            raw_response={},
            error="Schwab live orders require account hash mapping (configure in broker connection)",
        )

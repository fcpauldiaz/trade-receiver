import time
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from app.brokers.base import OptionContract, OrderResult
from app.config import settings

TRADER_BASE = "https://api.schwabapi.com/trader/v1"
AUTH_BASE = "https://api.schwabapi.com/v1"


class SchwabAdapter:
    name = "schwab"

    def __init__(
        self,
        access_token: str | None = None,
        account_hash: str | None = None,
        refresh_token: str | None = None,
        expires_at: float | None = None,
    ):
        self.access_token = access_token or ""
        self.account_hash = account_hash or ""
        self.refresh_token = refresh_token or ""
        self.expires_at = expires_at

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
        return f"{AUTH_BASE}/oauth/authorize?{params}"

    async def exchange_code(self, code: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{AUTH_BASE}/oauth/token",
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

    async def refresh_access_token(self) -> dict | None:
        if not self.refresh_token:
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{AUTH_BASE}/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": settings.schwab_client_id,
                    "client_secret": settings.schwab_client_secret,
                },
            )
            if resp.status_code >= 400:
                return None
            return resp.json()

    def token_needs_refresh(self) -> bool:
        if not self.expires_at:
            return False
        return time.time() >= self.expires_at - 60

    async def fetch_primary_account_hash(self) -> str | None:
        if not self.access_token:
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{TRADER_BASE}/accounts/accountNumbers",
                headers=self._headers(),
            )
            if resp.status_code >= 400:
                return None
            data = resp.json()
        if not data:
            return None
        first = data[0]
        return str(first.get("hashValue") or first.get("accountNumber") or "")

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

    async def get_positions(self) -> list[dict]:
        if not self.access_token or not self.account_hash:
            return []
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{TRADER_BASE}/accounts/{self.account_hash}",
                headers=self._headers(),
                params={"fields": "positions"},
            )
            if resp.status_code >= 400:
                return []
            data = resp.json()
        positions = []
        for acct in data if isinstance(data, list) else [data]:
            for pos in (acct.get("securitiesAccount") or {}).get("positions") or []:
                instrument = pos.get("instrument") or {}
                positions.append({
                    "symbol": instrument.get("symbol", ""),
                    "asset_type": instrument.get("assetType", ""),
                    "quantity": float(pos.get("longQuantity") or pos.get("shortQuantity") or 0),
                })
        return positions

    async def get_account_equity(self) -> Decimal | None:
        if not self.access_token or not self.account_hash:
            return Decimal("100000") if self.access_token else None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{TRADER_BASE}/accounts/{self.account_hash}",
                headers=self._headers(),
                params={"fields": "positions"},
            )
            if resp.status_code >= 400:
                return None
            data = resp.json()
        acct = data[0] if isinstance(data, list) and data else data
        balances = (acct.get("securitiesAccount") or {}).get("currentBalances") or {}
        equity = balances.get("liquidationValue") or balances.get("equity")
        if equity is None:
            return None
        return Decimal(str(equity))

    def _schwab_option_instruction(self, side: str) -> str:
        mapping = {
            "buy_to_open": "BUY_TO_OPEN",
            "sell_to_close": "SELL_TO_CLOSE",
            "buy": "BUY",
            "sell": "SELL",
        }
        return mapping.get(side, side.upper())

    async def _place_live_order(self, order_body: dict) -> OrderResult:
        if not self.account_hash:
            return OrderResult(
                success=False,
                order_id=None,
                fill_price=None,
                raw_response={},
                error="Schwab account hash missing — reconnect broker",
            )
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{TRADER_BASE}/accounts/{self.account_hash}/orders",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=order_body,
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
            order_id = str(body.get("orderId") or body.get("order_id") or "")
            return OrderResult(success=True, order_id=order_id, fill_price=None, raw_response=body)

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
        instruction = self._schwab_option_instruction(side)
        order_body = {
            "orderType": "MARKET",
            "session": "NORMAL",
            "duration": "DAY",
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [{
                "instruction": instruction,
                "quantity": quantity,
                "instrument": {"symbol": contract.symbol, "assetType": "OPTION"},
            }],
        }
        return await self._place_live_order(order_body)

    async def place_equity_order(self, symbol: str, quantity: int, side: str, mode: str) -> OrderResult:
        if mode == "paper":
            return OrderResult(
                success=True,
                order_id=f"paper-schwab-{symbol}",
                fill_price=None,
                raw_response={"simulated": True, "symbol": symbol, "quantity": quantity},
            )
        instruction = "BUY" if side.lower() == "buy" else "SELL"
        order_body = {
            "orderType": "MARKET",
            "session": "NORMAL",
            "duration": "DAY",
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [{
                "instruction": instruction,
                "quantity": quantity,
                "instrument": {"symbol": symbol.upper(), "assetType": "EQUITY"},
            }],
        }
        return await self._place_live_order(order_body)

    async def get_order_status(self, order_id: str) -> dict | None:
        if not self.account_hash or not order_id:
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{TRADER_BASE}/accounts/{self.account_hash}/orders/{order_id}",
                headers=self._headers(),
            )
            if resp.status_code >= 400:
                return None
            return resp.json()

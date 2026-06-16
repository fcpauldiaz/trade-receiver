import base64
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from app.brokers.base import BrokerAdapter, OptionContract, OrderResult
from app.config import settings


class TradierAdapter:
    name = "tradier"

    def __init__(self, access_token: str | None = None, account_id: str | None = None):
        if not access_token:
            raise ValueError("Tradier connection missing access token")
        self.access_token = access_token
        self.account_id = account_id or ""
        self.base = settings.tradier_api_base.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    @staticmethod
    def authorization_url(state: str) -> str:
        params = urlencode({
            "client_id": settings.tradier_client_id or "",
            "scope": settings.tradier_oauth_scope,
            "state": state,
        })
        oauth_base = settings.tradier_api_base.replace("/v1", "")
        return f"{oauth_base}/v1/oauth/authorize?{params}"

    @staticmethod
    async def exchange_code(code: str) -> dict:
        creds = base64.b64encode(
            f"{settings.tradier_client_id}:{settings.tradier_client_secret}".encode()
        ).decode()
        oauth_base = settings.tradier_api_base.replace("/v1", "")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{oauth_base}/v1/oauth/accesstoken",
                headers={
                    "Authorization": f"Basic {creds}",
                    "Accept": "application/json",
                },
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def fetch_primary_account_id(self) -> str | None:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base}/user/profile",
                headers=self._headers(),
            )
            if resp.status_code >= 400:
                return None
            data = resp.json()
        accounts = data.get("profile", {}).get("account", [])
        if isinstance(accounts, dict):
            accounts = [accounts]
        if not accounts:
            return None
        account = accounts[0]
        if isinstance(account, dict):
            return str(account.get("account_number") or account.get("id") or "")
        return None

    async def get_option_chain(
        self, underlying: str, expiration: date | None = None
    ) -> list[OptionContract]:
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
        if not self.account_id:
            return OrderResult(
                success=False,
                order_id=None,
                fill_price=None,
                raw_response={},
                error="Tradier account id missing on connection",
            )
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

    async def get_account_equity(self) -> Decimal | None:
        if not self.account_id:
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base}/accounts/{self.account_id}/balances",
                headers=self._headers(),
            )
            if resp.status_code >= 400:
                return None
            data = resp.json()
        balances = data.get("balances", {})
        total = balances.get("total_equity") or balances.get("total_cash")
        if total is None:
            return None
        return Decimal(str(total))

    async def place_equity_order(self, symbol: str, quantity: int, side: str, mode: str) -> OrderResult:
        if not self.account_id:
            return OrderResult(
                success=False,
                order_id=None,
                fill_price=None,
                raw_response={},
                error="Tradier account id missing on connection",
            )
        if mode == "paper" and "sandbox" not in self.base:
            return OrderResult(
                success=True,
                order_id=f"paper-{symbol}",
                fill_price=None,
                raw_response={"simulated": True, "mode": "paper", "symbol": symbol, "quantity": quantity},
            )
        data = {
            "class": "equity",
            "symbol": symbol.upper(),
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
            return OrderResult(
                success=True,
                order_id=order_id,
                fill_price=None,
                raw_response=body,
            )

    async def get_positions(self) -> list[dict]:
        if not self.account_id:
            return []
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base}/accounts/{self.account_id}/positions",
                headers=self._headers(),
            )
            if resp.status_code >= 400:
                return []
            data = resp.json()
        positions = []
        for pos in data.get("positions", {}).get("position", []) or []:
            if isinstance(pos, dict):
                positions.append({
                    "symbol": pos.get("symbol", ""),
                    "asset_type": "OPTION" if pos.get("option_symbol") else "EQUITY",
                    "quantity": float(pos.get("quantity") or 0),
                })
        return positions

    async def get_order_status(self, order_id: str) -> dict | None:
        if not self.account_id or not order_id:
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base}/accounts/{self.account_id}/orders/{order_id}",
                headers=self._headers(),
            )
            if resp.status_code >= 400:
                return None
            data = resp.json()
        order = data.get("order", data)
        status = str(order.get("status", "")).upper()
        return {"status": "FILLED" if status == "FILLED" else status, "order": order}

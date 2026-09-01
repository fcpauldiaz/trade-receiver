import json
import secrets
from typing import Any

import httpx

from app.brokers.base import BrokerAdapter, OptionContract, OrderResult, TradeMode
from app.schemas.trade import FuturesAction, NinjaTraderOrderPayload, ValidatedFuturesTrade


class NinjaTraderAdapter:
    name = "ninjatrader"

    def __init__(
        self,
        *,
        forward_url: str,
        webhook_secret: str = "",
    ) -> None:
        self.forward_url = forward_url.rstrip("/")
        self.webhook_secret = webhook_secret

    @classmethod
    def from_credentials(cls, raw: str) -> "NinjaTraderAdapter":
        raw = raw.strip()
        forward_url = ""
        webhook_secret = ""
        if raw.startswith("{"):
            data = json.loads(raw)
            forward_url = str(data.get("forward_url") or "")
            webhook_secret = str(data.get("webhook_secret") or "")
        else:
            forward_url = raw
        return cls(forward_url=forward_url, webhook_secret=webhook_secret)

    async def get_option_chain(
        self, underlying: str, expiration=None
    ) -> list[OptionContract]:
        return []

    async def preview_order(
        self, contract: OptionContract, quantity: int, side: str
    ) -> OrderResult:
        return OrderResult(success=False, order_id=None, fill_price=None, raw_response={}, error="unsupported")

    async def place_order(
        self, contract: OptionContract, quantity: int, side: str, mode: TradeMode
    ) -> OrderResult:
        return OrderResult(
            success=False,
            order_id=None,
            fill_price=None,
            raw_response={},
            error="NinjaTrader adapter uses execute_futures_order",
        )

    async def place_order_with_take_profit(
        self,
        contract: OptionContract,
        quantity: int,
        side: str,
        mode: TradeMode,
        *,
        take_profit_price,
    ) -> OrderResult:
        return OrderResult(
            success=False,
            order_id=None,
            fill_price=None,
            raw_response={},
            error="NinjaTrader adapter uses execute_futures_order",
        )

    async def get_account_equity(self):
        return None

    async def place_equity_order(
        self, symbol: str, quantity: int, side: str, mode: TradeMode
    ) -> OrderResult:
        action: FuturesAction = "BUY" if side.lower() in {"buy", "buy_to_open"} else "SELL"
        validated = ValidatedFuturesTrade(
            action=action,
            symbol=symbol.upper(),
            quantity=quantity,
            confidence=1.0,
            rationale="broker test order",
            broker="ninjatrader",
            external_id=f"test-{secrets.token_hex(4)}",
        )
        return await self.execute_futures_order(validated, mode=mode, dry_run=True)

    async def get_positions(self) -> list[dict]:
        return []

    async def get_order_status(self, order_id: str) -> dict | None:
        return {"status": "SUBMITTED", "order_id": order_id}

    def build_order_payload(
        self,
        validated: ValidatedFuturesTrade,
        *,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        payload = NinjaTraderOrderPayload(
            id=validated.external_id or secrets.token_hex(8),
            symbol=validated.symbol,
            action=validated.action,
            orderType=validated.order_type,
            quantity=validated.quantity,
            stopLossTicks=validated.stop_loss_ticks,
            profitTargetTicks=validated.profit_target_ticks,
            dryRun=dry_run,
        )
        return payload.model_dump(exclude_none=True)

    async def execute_futures_order(
        self,
        validated: ValidatedFuturesTrade,
        *,
        mode: TradeMode = "paper",
        dry_run: bool | None = None,
    ) -> OrderResult:
        if not self.forward_url:
            return OrderResult(
                success=False,
                order_id=None,
                fill_price=None,
                raw_response={},
                error="NinjaTrader forward URL is not configured",
            )

        effective_dry_run = dry_run if dry_run is not None else mode == "paper"
        body = self.build_order_payload(validated, dry_run=effective_dry_run)
        headers = {"Content-Type": "application/json"}
        if self.webhook_secret:
            headers["X-Webhook-Secret"] = self.webhook_secret

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(self.forward_url, headers=headers, json=body)
                raw: dict[str, Any]
                try:
                    raw = resp.json()
                except Exception:
                    raw = {"status_code": resp.status_code, "text": resp.text[:500]}
                success = 200 <= resp.status_code < 300
                order_id = None
                if isinstance(raw, dict):
                    order_id = str(raw.get("order_id") or raw.get("id") or body["id"])
                else:
                    order_id = body["id"]
                if effective_dry_run:
                    raw = {**raw, "simulated": True, "dry_run": True}
                return OrderResult(
                    success=success,
                    order_id=order_id,
                    fill_price=None,
                    raw_response=raw,
                    error=None if success else f"forward returned HTTP {resp.status_code}",
                )
        except httpx.HTTPError as exc:
            return OrderResult(
                success=False,
                order_id=None,
                fill_price=None,
                raw_response={},
                error=str(exc),
            )


def pack_ninjatrader_credentials(forward_url: str, webhook_secret: str | None = None) -> str:
    payload: dict[str, str] = {"forward_url": forward_url.strip()}
    if webhook_secret:
        payload["webhook_secret"] = webhook_secret.strip()
    return json.dumps(payload)

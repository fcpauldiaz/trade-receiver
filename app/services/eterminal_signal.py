"""Map eterminal Chrome extension WebhookEnvelope → TradeIntent."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.config import settings
from app.schemas.trade import TradeIntent
from app.services.market_hours import ET


def is_eterminal_envelope(body: dict[str, Any]) -> bool:
    if body.get("type") is None:
        return False
    return "firedAt" in body or "signal" in body or "context" in body


def eterminal_idempotency_key(user_id: str, body: dict[str, Any]) -> str:
    signal = body.get("signal") if isinstance(body.get("signal"), dict) else {}
    signal_id = str(signal.get("id") or "")
    raw = json.dumps(
        {
            "user_id": user_id,
            "source": "eterminal",
            "type": body.get("type"),
            "signal_id": signal_id,
            "firedAt": body.get("firedAt"),
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _et_today() -> date:
    return datetime.now(ET).date()


def _round_strike_to_5(price: Decimal) -> Decimal:
    if price <= 0:
        return Decimal("0")
    return (price / Decimal("5")).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal("5")


def _target_price(body: dict[str, Any]) -> Decimal:
    signal = body.get("signal") if isinstance(body.get("signal"), dict) else {}
    context = body.get("context") if isinstance(body.get("context"), dict) else {}
    for raw in (signal.get("price"), context.get("currentPrice")):
        if raw is None:
            continue
        try:
            value = Decimal(str(raw))
        except Exception:
            continue
        if value > 0:
            return value
    return Decimal("0")


def map_eterminal_signal(body: dict[str, Any]) -> TradeIntent | None:
    """Return TradeIntent for tradable signals, or None when the event should skip."""
    if body.get("type") != "signal":
        return None

    signal = body.get("signal")
    if not isinstance(signal, dict):
        return None

    side = str(signal.get("side") or "").strip().lower()
    if side not in {"long", "short"}:
        return None

    target = _target_price(body)
    if target <= 0:
        return None

    option_type = "call" if side == "long" else "put"
    strike = _round_strike_to_5(target)
    if strike <= 0:
        return None

    return TradeIntent(
        action="buy_to_open",
        underlying="SPX",
        option_type=option_type,
        strike=strike,
        expiration=_et_today(),
        quantity=1,
        order_type="market",
        confidence=1.0,
        rationale=f"eterminal signal {signal.get('id', '')} {side} → SPX {option_type}",
        take_profit_pct=Decimal(str(settings.eterminal_take_profit_pct)),
        source="eterminal",
        notional_usd=Decimal(str(settings.eterminal_notional_usd)),
    )

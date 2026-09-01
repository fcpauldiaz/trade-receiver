import re
import uuid
from decimal import Decimal
from typing import Any

from app.schemas.trade import FuturesAction, TradeIntent

FUTURES_SYMBOLS = {
    "ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K",
    "CL", "MCL", "GC", "MGC", "SI", "SIL",
}

_STRUCTURED_ACTIONS = frozenset({"BUY", "SELL"})


def is_futures_order_payload(body: dict[str, Any]) -> bool:
    action = str(body.get("action", "")).upper()
    symbol = str(body.get("symbol", "")).strip()
    return action in _STRUCTURED_ACTIONS and bool(symbol)


def map_futures_order_payload(body: dict[str, Any]) -> TradeIntent:
    action_raw = str(body.get("action", "")).upper()
    action: FuturesAction = "BUY" if action_raw == "BUY" else "SELL"
    trade_action = "buy_to_open" if action == "BUY" else "sell_to_close"
    symbol = str(body.get("symbol", "")).strip().upper()
    quantity = int(body.get("quantity") or 1)
    order_type_raw = str(body.get("orderType") or body.get("order_type") or "MARKET").upper()
    order_type = "limit" if order_type_raw == "LIMIT" else "market"
    stop_loss = body.get("stopLossTicks", body.get("stop_loss_ticks"))
    profit_target = body.get("profitTargetTicks", body.get("profit_target_ticks"))
    external_id = str(body.get("id") or uuid.uuid4())

    return TradeIntent(
        action=trade_action,
        asset_class="future",
        underlying=symbol,
        quantity=max(1, quantity),
        order_type=order_type,
        confidence=1.0,
        rationale="structured futures webhook",
        source="futures_json",
        stop_loss_ticks=int(stop_loss) if stop_loss is not None else None,
        profit_target_ticks=int(profit_target) if profit_target is not None else None,
        external_id=external_id,
    )


def parse_futures_alert_rules(text: str) -> TradeIntent | None:
    upper = text.upper()
    action: FuturesAction | None = None
    if re.search(r"\bBUY\b", upper):
        action = "BUY"
    elif re.search(r"\bSELL\b", upper):
        action = "SELL"
    if action is None:
        return None

    symbol = ""
    for match in re.finditer(r"\b([A-Z]{2,4})\b", upper):
        token = match.group(1)
        if token in FUTURES_SYMBOLS:
            symbol = token
            break
    if not symbol:
        return None

    trade_action = "buy_to_open" if action == "BUY" else "sell_to_close"
    qty_match = re.search(r"\b(?:QTY|QUANTITY)\s*[:=]?\s*(\d+)\b", upper)
    quantity = int(qty_match.group(1)) if qty_match else 1
    stop_match = re.search(r"\b(?:SL|STOP)\s*[:=]?\s*(\d+)\s*(?:TICKS?)?\b", upper)
    tp_match = re.search(r"\b(?:TP|TARGET|PT)\s*[:=]?\s*(\d+)\s*(?:TICKS?)?\b", upper)

    return TradeIntent(
        action=trade_action,
        asset_class="future",
        underlying=symbol,
        quantity=max(1, quantity),
        confidence=0.85,
        rationale="rule-based futures parse",
        stop_loss_ticks=int(stop_match.group(1)) if stop_match else None,
        profit_target_ticks=int(tp_match.group(1)) if tp_match else None,
    )


def validate_futures_intent(intent: TradeIntent) -> list[str]:
    errors: list[str] = []
    symbol = intent.underlying.upper()
    if not symbol:
        errors.append("missing futures symbol")
    if intent.quantity < 1:
        errors.append("invalid quantity")
    if intent.action not in {"buy_to_open", "sell_to_close"}:
        errors.append("invalid futures action")
    return errors


def intent_to_validated_futures(intent: TradeIntent, broker_name: str = "ninjatrader") -> "ValidatedFuturesTrade":
    from app.schemas.trade import ValidatedFuturesTrade

    errors = validate_futures_intent(intent)
    action: FuturesAction = "BUY" if intent.action == "buy_to_open" else "SELL"
    order_type = "LIMIT" if intent.order_type == "limit" else "MARKET"
    return ValidatedFuturesTrade(
        action=action,
        symbol=intent.underlying.upper(),
        quantity=intent.quantity,
        order_type=order_type,
        stop_loss_ticks=intent.stop_loss_ticks,
        profit_target_ticks=intent.profit_target_ticks,
        confidence=intent.confidence,
        rationale=intent.rationale,
        broker=broker_name,  # type: ignore[arg-type]
        external_id=intent.external_id or str(uuid.uuid4()),
        validation_errors=errors,
    )

import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter
from app.models.tables import TradeExecution


def _extract_fill_price(order_status: dict | None, fallback: Decimal | None) -> float | None:
    if not order_status:
        return float(fallback) if fallback else None
    for activity in order_status.get("orderActivityCollection") or []:
        legs = activity.get("executionLegs") or []
        for leg in legs:
            price = leg.get("price")
            if price is not None:
                return float(price)
    filled = order_status.get("filledQuantity") or order_status.get("quantity")
    price = order_status.get("price")
    if price is not None:
        return float(price)
    if filled and fallback:
        return float(fallback)
    return float(fallback) if fallback else None


async def resolve_fill(adapter: BrokerAdapter, order_id: str | None, fallback: Decimal | None) -> tuple[str, float | None]:
    if not order_id:
        return "submitted", float(fallback) if fallback else None
    if not hasattr(adapter, "get_order_status"):
        return "filled", float(fallback) if fallback else None
    status_data = await adapter.get_order_status(order_id)
    if not status_data:
        return "submitted", float(fallback) if fallback else None
    state = str(status_data.get("status", "")).upper()
    fill_price = _extract_fill_price(status_data, fallback)
    if state in ("FILLED", "EXECUTED"):
        return "filled", fill_price
    if state in ("CANCELED", "REJECTED", "EXPIRED"):
        return "failed", fill_price
    return "submitted", fill_price


def compute_close_pnl(
    db: Session,
    user_id: str,
    contract_symbol: str,
    quantity: int,
    fill_price: float | None,
) -> float | None:
    if fill_price is None or not contract_symbol:
        return None
    opens = list(
        db.scalars(
            select(TradeExecution).where(
                TradeExecution.user_id == user_id,
                TradeExecution.contract_symbol == contract_symbol,
                TradeExecution.status == "filled",
            ).order_by(TradeExecution.created_at.asc())
        )
    )
    open_cost = 0.0
    open_qty = 0
    for row in opens:
        if row.intent_json and "sell_to_close" in row.intent_json:
            continue
        if row.fill_price:
            open_cost += row.fill_price * row.quantity * 100
            open_qty += row.quantity
    if open_qty <= 0:
        return None
    avg_open = open_cost / (open_qty * 100)
    return round((fill_price - avg_open) * quantity * 100, 2)

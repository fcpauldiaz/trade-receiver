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
    price = order_status.get("price")
    if price is not None:
        return float(price)
    avg = order_status.get("avg_fill_price") or order_status.get("average_price")
    if avg is not None:
        return float(avg)
    filled = order_status.get("filledQuantity") or order_status.get("quantity")
    if filled and fallback:
        return float(fallback)
    return float(fallback) if fallback else None


async def resolve_fill(
    adapter: BrokerAdapter, order_id: str | None, fallback: Decimal | None
) -> tuple[str, float | None]:
    if not order_id:
        return "submitted", float(fallback) if fallback else None
    if not hasattr(adapter, "get_order_status"):
        return "filled", float(fallback) if fallback else None
    status_data = await adapter.get_order_status(order_id)
    if not status_data:
        return "submitted", float(fallback) if fallback else None
    order = status_data.get("order") if isinstance(status_data.get("order"), dict) else status_data
    payload = order or status_data
    state = str(payload.get("status", "")).upper()
    fill_price = _extract_fill_price(payload, fallback)
    if state in ("FILLED", "EXECUTED"):
        return "filled", fill_price
    if state in ("CANCELED", "CANCELLED", "REJECTED", "EXPIRED"):
        return "failed", fill_price
    if state in ("OPEN", "PENDING", "PARTIALLY_FILLED", "WORKING"):
        return "submitted", fill_price
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
            select(TradeExecution)
            .where(
                TradeExecution.user_id == user_id,
                TradeExecution.contract_symbol == contract_symbol,
                TradeExecution.status == "filled",
            )
            .order_by(TradeExecution.created_at.asc())
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


async def reconcile_submitted_closes(
    db: Session,
    user_id: str,
    adapter: BrokerAdapter,
    *,
    limit: int = 20,
) -> int:
    """Sync resting take-profit / STC legs from broker into filled + realized pnl."""
    rows = list(
        db.scalars(
            select(TradeExecution)
            .where(
                TradeExecution.user_id == user_id,
                TradeExecution.status == "submitted",
            )
            .order_by(TradeExecution.created_at.asc())
            .limit(limit)
        )
    )
    updated = 0
    for row in rows:
        if not row.broker_order_id:
            continue
        if not (row.intent_json and "sell_to_close" in row.intent_json):
            continue
        fallback = Decimal(str(row.fill_price)) if row.fill_price is not None else None
        status, fill_price = await resolve_fill(adapter, row.broker_order_id, fallback)
        if status == "submitted":
            continue
        row.status = status
        if fill_price is not None:
            row.fill_price = fill_price
        if status == "filled":
            row.pnl = compute_close_pnl(
                db,
                user_id,
                row.contract_symbol or "",
                row.quantity,
                fill_price,
            )
        updated += 1
    if updated:
        db.commit()
    return updated

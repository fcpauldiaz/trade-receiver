from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import TradeExecution


def list_trades(
    db: Session,
    user_id: str,
    *,
    mode: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    limit: int = 100,
) -> list[TradeExecution]:
    stmt = select(TradeExecution).where(TradeExecution.user_id == user_id)
    if mode:
        stmt = stmt.where(TradeExecution.mode == mode)
    if from_dt:
        stmt = stmt.where(TradeExecution.created_at >= from_dt)
    if to_dt:
        stmt = stmt.where(TradeExecution.created_at <= to_dt)
    stmt = stmt.order_by(TradeExecution.created_at.desc()).limit(limit)
    return list(db.scalars(stmt))


def trade_cashflow(row: TradeExecution) -> float | None:
    """Dashboard cash impact for a filled trade.

    Prefer stored realized pnl (closes). For open fills with no pnl yet, use
    premium paid as a negative cashflow so calendars/KPIs are not blank zeros.
    """
    if row.status != "filled":
        return None
    if row.pnl is not None:
        return float(row.pnl)
    if row.fill_price is None or row.quantity <= 0:
        return None
    intent = row.intent_json or ""
    if "sell_to_close" in intent:
        return 0.0
    return round(-float(row.fill_price) * row.quantity * 100, 2)


def daily_pnl(db: Session, user_id: str, month: str) -> dict[str, float]:
    year, mon = map(int, month.split("-"))
    start = datetime(year, mon, 1)
    if mon == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, mon + 1, 1)

    rows = db.scalars(
        select(TradeExecution).where(
            TradeExecution.user_id == user_id,
            TradeExecution.created_at >= start,
            TradeExecution.created_at < end,
            TradeExecution.status == "filled",
        )
    ).all()

    by_day: dict[str, float] = {}
    for row in rows:
        cash = trade_cashflow(row)
        if cash is None:
            continue
        day = row.created_at.strftime("%Y-%m-%d")
        by_day[day] = round(by_day.get(day, 0.0) + cash, 2)
    return by_day


def performance_summary(db: Session, user_id: str) -> dict:
    rows = list(
        db.scalars(
            select(TradeExecution).where(
                TradeExecution.user_id == user_id,
                TradeExecution.status == "filled",
            )
        )
    )
    cashflows = [trade_cashflow(r) for r in rows]
    cashflows = [c for c in cashflows if c is not None]
    total_pnl = round(sum(cashflows), 2)

    realized = [r for r in rows if r.pnl is not None]
    wins = sum(1 for r in realized if (r.pnl or 0) > 0)
    win_rate = (wins / len(realized) * 100) if realized else 0.0

    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)
    mtd_pnl = 0.0
    for row in rows:
        if row.created_at < month_start:
            continue
        cash = trade_cashflow(row)
        if cash is not None:
            mtd_pnl += cash

    return {
        "total_trades": len(rows),
        "total_pnl": total_pnl,
        "mtd_pnl": round(mtd_pnl, 2),
        "win_rate": round(win_rate, 2),
    }

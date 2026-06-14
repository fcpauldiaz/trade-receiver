from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, select
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

    by_day: dict[str, float] = defaultdict(float)
    for row in rows:
        day = row.created_at.strftime("%Y-%m-%d")
        by_day[day] += float(row.pnl or 0)
    return dict(by_day)


def performance_summary(db: Session, user_id: str) -> dict:
    rows = list(
        db.scalars(
            select(TradeExecution).where(
                TradeExecution.user_id == user_id,
                TradeExecution.status == "filled",
            )
        )
    )
    total_pnl = sum(float(r.pnl or 0) for r in rows)
    wins = sum(1 for r in rows if (r.pnl or 0) > 0)
    total = len(rows)
    win_rate = (wins / total * 100) if total else 0.0
    return {
        "total_trades": total,
        "total_pnl": total_pnl,
        "win_rate": round(win_rate, 2),
    }

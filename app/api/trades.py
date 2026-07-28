from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.tables import BrokerConnection, TradeExecution, User
from app.services.option_chain import get_adapter
from app.services.order_lifecycle import reconcile_submitted_closes
from app.services.performance import daily_pnl, list_trades, performance_summary

router = APIRouter(prefix="/v1/me", tags=["trades"])


class TradeResponse(BaseModel):
    id: str
    broker: str
    mode: str
    status: str
    underlying: str
    option_type: str
    strike: float
    expiration: str
    quantity: int
    fill_price: float | None
    pnl: float | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: TradeExecution) -> "TradeResponse":
        return cls(
            id=row.id,
            broker=row.broker,
            mode=row.mode,
            status=row.status,
            underlying=row.underlying,
            option_type=row.option_type,
            strike=row.strike,
            expiration=row.expiration,
            quantity=row.quantity,
            fill_price=row.fill_price,
            pnl=row.pnl,
            created_at=row.created_at,
        )


def _default_connection(db: Session, user: User) -> BrokerConnection | None:
    if user.default_broker:
        conn = (
            db.query(BrokerConnection)
            .filter_by(user_id=user.id, broker=user.default_broker, status="connected")
            .first()
        )
        if conn:
            return conn
    return db.query(BrokerConnection).filter_by(user_id=user.id, status="connected").first()


async def _reconcile_if_possible(db: Session, user: User) -> None:
    conn = _default_connection(db, user)
    if conn is None:
        return
    try:
        adapter = await get_adapter(db, conn)
        await reconcile_submitted_closes(db, user.id, adapter)
    except Exception:
        # Listing/performance should still work if broker status checks fail.
        return


@router.get("/trades", response_model=list[TradeResponse])
async def get_trades(
    mode: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    await _reconcile_if_possible(db, user)
    rows = list_trades(db, user.id, mode=mode, limit=limit)
    return [TradeResponse.from_row(r) for r in rows]


@router.get("/performance/daily")
async def get_daily_performance(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    await _reconcile_if_possible(db, user)
    return daily_pnl(db, user.id, month)


@router.get("/performance/summary")
async def get_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    await _reconcile_if_possible(db, user)
    return performance_summary(db, user.id)

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.tables import TradeExecution, User
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


@router.get("/trades", response_model=list[TradeResponse])
def get_trades(
    mode: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = list_trades(db, user.id, mode=mode, limit=limit)
    return [TradeResponse.from_row(r) for r in rows]


@router.get("/performance/daily")
def get_daily_performance(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return daily_pnl(db, user.id, month)


@router.get("/performance/summary")
def get_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return performance_summary(db, user.id)

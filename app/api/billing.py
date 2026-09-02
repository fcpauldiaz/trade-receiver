from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.tables import User
from app.services.entitlements import can_process_trades

router = APIRouter(prefix="/v1", tags=["billing"])


class BillingStatus(BaseModel):
    status: str
    plan_name: str
    renews_at: datetime | None
    ends_at: datetime | None
    can_process_trades: bool


@router.get("/me/billing", response_model=BillingStatus)
def get_billing(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    del db
    sub = user.subscription
    return BillingStatus(
        status=sub.status if sub else "none",
        plan_name=sub.plan_name if sub else "free",
        renews_at=sub.renews_at if sub else None,
        ends_at=sub.ends_at if sub else None,
        can_process_trades=can_process_trades(user),
    )

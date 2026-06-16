from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.tables import User

router = APIRouter(prefix="/v1/me", tags=["settings"])

SizingMode = Literal["alert_inferred", "fixed", "risk_percent"]


class UserSettings(BaseModel):
    default_mode: str
    max_contracts: int
    allowed_tickers: str | None
    live_trading_enabled: bool
    sizing_mode: SizingMode = "alert_inferred"
    fixed_contracts: int = Field(ge=1, le=100, default=1)
    risk_percent: float = Field(gt=0, le=100, default=1.0)
    default_broker: str | None = None


@router.get("/settings", response_model=UserSettings)
def get_settings(user: User = Depends(get_current_user)):
    return UserSettings(
        default_mode=user.default_mode,
        max_contracts=user.max_contracts,
        allowed_tickers=user.allowed_tickers,
        live_trading_enabled=user.live_trading_enabled,
        sizing_mode=user.sizing_mode,  # type: ignore[arg-type]
        fixed_contracts=user.fixed_contracts,
        risk_percent=user.risk_percent,
        default_broker=user.default_broker,
    )


@router.put("/settings", response_model=UserSettings)
def update_settings(
    body: UserSettings,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user.default_mode = body.default_mode
    user.max_contracts = body.max_contracts
    user.allowed_tickers = body.allowed_tickers
    user.live_trading_enabled = body.live_trading_enabled
    user.sizing_mode = body.sizing_mode
    user.fixed_contracts = body.fixed_contracts
    user.risk_percent = body.risk_percent
    if body.default_broker is not None:
        user.default_broker = body.default_broker
    db.commit()
    return body


@router.post("/onboarding/complete")
def complete_onboarding(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.onboarding_completed = True
    db.commit()
    return {"status": "completed"}

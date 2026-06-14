from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.tables import User

router = APIRouter(prefix="/v1/me", tags=["settings"])


class UserSettings(BaseModel):
    default_mode: str
    max_contracts: int
    allowed_tickers: str | None
    live_trading_enabled: bool


@router.get("/settings", response_model=UserSettings)
def get_settings(user: User = Depends(get_current_user)):
    return UserSettings(
        default_mode=user.default_mode,
        max_contracts=user.max_contracts,
        allowed_tickers=user.allowed_tickers,
        live_trading_enabled=user.live_trading_enabled,
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
    db.commit()
    return body

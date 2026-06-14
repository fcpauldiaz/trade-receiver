from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.brokers.schwab import SchwabAdapter
from app.brokers.tradier import TradierAdapter
from app.config import settings
from app.database import get_db
from app.models.tables import BrokerConnection, User
from app.services.crypto import encrypt_value
from app.services.entitlements import can_process_trades
from app.services.oauth_state import create_oauth_state, oauth_success_redirect, verify_oauth_state

router = APIRouter(prefix="/v1/me/brokers", tags=["brokers"])


class BrokerStatus(BaseModel):
    broker: str
    status: str
    account_id: str | None


def _upsert_connection(
    db: Session,
    user: User,
    broker: str,
    *,
    access_token: str,
    account_id: str | None = None,
) -> BrokerConnection:
    conn = db.query(BrokerConnection).filter_by(user_id=user.id, broker=broker).first()
    if conn is None:
        conn = BrokerConnection(user_id=user.id, broker=broker)
        db.add(conn)
    conn.encrypted_credentials = encrypt_value(access_token)
    if account_id:
        conn.account_id = account_id
    conn.status = "connected"
    db.commit()
    return conn


@router.get("", response_model=list[BrokerStatus])
def list_brokers(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(BrokerConnection).filter(BrokerConnection.user_id == user.id).all()
    return [
        BrokerStatus(broker=r.broker, status=r.status, account_id=r.account_id)
        for r in rows
    ]


@router.delete("/{broker}")
def disconnect_broker(broker: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conn = db.query(BrokerConnection).filter_by(user_id=user.id, broker=broker).first()
    if conn:
        db.delete(conn)
        db.commit()
    return {"status": "disconnected"}


@router.get("/tradier/authorize")
def tradier_authorize(user: User = Depends(get_current_user)):
    if not can_process_trades(user):
        raise HTTPException(status_code=402, detail="Active subscription required")
    if not settings.tradier_client_id or not settings.tradier_client_secret:
        raise HTTPException(status_code=503, detail="Tradier OAuth is not configured on the server")
    state = create_oauth_state(user.id, "tradier")
    return {"url": TradierAdapter.authorization_url(state)}


@router.get("/tradier/callback")
async def tradier_callback(code: str, state: str, db: Session = Depends(get_db)):
    try:
        user_id = verify_oauth_state(state, "tradier")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="User not found for OAuth state")

    tokens = await TradierAdapter.exchange_code(code)
    access_token = str(tokens.get("access_token", ""))
    if not access_token:
        raise HTTPException(status_code=400, detail="Tradier did not return an access token")

    adapter = TradierAdapter(access_token=access_token)
    account_id = await adapter.fetch_primary_account_id()
    _upsert_connection(db, user, "tradier", access_token=access_token, account_id=account_id)
    return RedirectResponse(url=oauth_success_redirect("tradier"))


@router.get("/schwab/authorize")
def schwab_authorize(user: User = Depends(get_current_user)):
    if not can_process_trades(user):
        raise HTTPException(status_code=402, detail="Active subscription required")
    if not settings.schwab_client_id or not settings.schwab_client_secret:
        raise HTTPException(status_code=503, detail="Schwab OAuth is not configured on the server")
    state = create_oauth_state(user.id, "schwab")
    return {"url": SchwabAdapter.authorization_url(state)}


@router.get("/schwab/callback")
async def schwab_callback(code: str, state: str, db: Session = Depends(get_db)):
    try:
        user_id = verify_oauth_state(state, "schwab")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="User not found for OAuth state")

    adapter = SchwabAdapter()
    tokens = await adapter.exchange_code(code)
    access_token = str(tokens.get("access_token", ""))
    if not access_token:
        raise HTTPException(status_code=400, detail="Schwab did not return an access token")

    _upsert_connection(db, user, "schwab", access_token=access_token)
    return RedirectResponse(url=oauth_success_redirect("schwab"))

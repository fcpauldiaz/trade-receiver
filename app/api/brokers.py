import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.brokers.schwab import SchwabAdapter
from app.config import settings
from app.database import get_db
from app.models.tables import BrokerConnection, User
from app.services.crypto import encrypt_value
from app.services.entitlements import can_process_trades

router = APIRouter(prefix="/v1/me/brokers", tags=["brokers"])


class BrokerStatus(BaseModel):
    broker: str
    status: str
    account_id: str | None


class TradierConnect(BaseModel):
    access_token: str
    account_id: str


@router.get("", response_model=list[BrokerStatus])
def list_brokers(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(BrokerConnection).filter(BrokerConnection.user_id == user.id).all()
    return [
        BrokerStatus(broker=r.broker, status=r.status, account_id=r.account_id)
        for r in rows
    ]


@router.post("/tradier")
def connect_tradier(
    body: TradierConnect,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not can_process_trades(user):
        raise HTTPException(status_code=402, detail="Active subscription required")
    conn = db.query(BrokerConnection).filter_by(user_id=user.id, broker="tradier").first()
    if conn is None:
        conn = BrokerConnection(user_id=user.id, broker="tradier")
        db.add(conn)
    conn.encrypted_credentials = encrypt_value(body.access_token)
    conn.account_id = body.account_id
    conn.status = "connected"
    db.commit()
    return {"status": "connected"}


@router.delete("/{broker}")
def disconnect_broker(broker: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conn = db.query(BrokerConnection).filter_by(user_id=user.id, broker=broker).first()
    if conn:
        db.delete(conn)
        db.commit()
    return {"status": "disconnected"}


@router.get("/schwab/authorize")
def schwab_authorize(user: User = Depends(get_current_user)):
    if not can_process_trades(user):
        raise HTTPException(status_code=402, detail="Active subscription required")
    state = hashlib.sha256(user.id.encode()).hexdigest()[:16]
    return {"url": SchwabAdapter.authorization_url(state)}


@router.get("/schwab/callback")
async def schwab_callback(code: str, state: str, db: Session = Depends(get_db)):
    adapter = SchwabAdapter()
    tokens = await adapter.exchange_code(code)
    user = db.query(User).filter(User.id.like(f"%{state}%")).first()
    if user is None:
        users = db.query(User).all()
        user = users[0] if users else None
    if user is None:
        raise HTTPException(status_code=400, detail="User not found for OAuth state")
    conn = db.query(BrokerConnection).filter_by(user_id=user.id, broker="schwab").first()
    if conn is None:
        conn = BrokerConnection(user_id=user.id, broker="schwab")
        db.add(conn)
    token = tokens.get("access_token", "")
    if token:
        conn.encrypted_credentials = encrypt_value(token)
    conn.status = "connected"
    db.commit()
    return RedirectResponse(url=f"{settings.receiver_base_url}/oauth/schwab/success")

import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.brokers.ninjatrader import (
    NinjaTraderAdapter,
    ninjatrader_futures_symbol,
    pack_ninjatrader_credentials,
)
from app.brokers.schwab import SchwabAdapter
from app.brokers.tradier import TradierAdapter
from app.brokers.tradier_env import TradierEnvironment, normalize_tradier_environment
from app.config import settings
from app.database import get_db
from app.models.tables import BrokerConnection, User
from app.services.broker_credentials import pack_credentials
from app.services.crypto import decrypt_value, encrypt_value
from app.services import market_hours
from app.services.entitlements import can_process_trades
from app.services.oauth_state import create_oauth_state, oauth_success_redirect, verify_oauth_state
from app.services.option_chain import get_adapter

router = APIRouter(prefix="/v1/me/brokers", tags=["brokers"])


class BrokerStatus(BaseModel):
    broker: str
    status: str
    account_id: str | None
    environment: str | None = None
    forward_url: str | None = None


class DefaultBrokerRequest(BaseModel):
    broker: str


class TradierTokenConnectRequest(BaseModel):
    access_token: str
    account_id: str | None = None
    environment: TradierEnvironment = "sandbox"


class TradierTokenConnectResponse(BaseModel):
    broker: str
    status: str
    account_id: str | None
    environment: TradierEnvironment


class TestOrderRequest(BaseModel):
    symbol: str = "SPY"
    quantity: int = 1
    side: str = "buy"
    action: str | None = None
    dry_run: bool | None = None


class NinjaTraderConnectRequest(BaseModel):
    forward_url: str
    webhook_secret: str | None = None
    account_label: str | None = None


class NinjaTraderConnectResponse(BaseModel):
    broker: str
    status: str
    forward_url: str


class TestOrderResponse(BaseModel):
    success: bool
    broker: str
    mode: str
    order_id: str | None
    simulated: bool
    message: str


def _apply_tradier_trading_prefs(user: User, environment: TradierEnvironment) -> None:
    if environment == "sandbox":
        user.default_mode = "paper"
        return
    user.default_mode = "live"
    user.live_trading_enabled = True


def _upsert_connection(
    db: Session,
    user: User,
    broker: str,
    *,
    credentials: str,
    account_id: str | None = None,
    environment: str | None = None,
) -> BrokerConnection:
    conn = db.query(BrokerConnection).filter_by(user_id=user.id, broker=broker).first()
    if conn is None:
        conn = BrokerConnection(user_id=user.id, broker=broker)
        db.add(conn)
    conn.encrypted_credentials = encrypt_value(credentials)
    if account_id:
        conn.account_id = account_id
    if environment is not None:
        conn.environment = environment
    conn.status = "connected"
    if user.default_broker is None:
        user.default_broker = broker
    db.commit()
    return conn


def _ninjatrader_forward_url(conn: BrokerConnection) -> str | None:
    if conn.broker != "ninjatrader" or not conn.encrypted_credentials:
        return None
    try:
        raw = decrypt_value(conn.encrypted_credentials)
        adapter = NinjaTraderAdapter.from_credentials(raw)
        return adapter.forward_url or None
    except Exception:
        return None


@router.get("", response_model=list[BrokerStatus])
def list_brokers(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(BrokerConnection).filter(BrokerConnection.user_id == user.id).all()
    return [
        BrokerStatus(
            broker=r.broker,
            status=r.status,
            account_id=r.account_id,
            environment=r.environment,
            forward_url=_ninjatrader_forward_url(r),
        )
        for r in rows
    ]


@router.put("/default")
def set_default_broker(
    body: DefaultBrokerRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conn = db.query(BrokerConnection).filter_by(
        user_id=user.id, broker=body.broker, status="connected"
    ).first()
    if conn is None:
        raise HTTPException(status_code=404, detail="Broker not connected")
    user.default_broker = body.broker
    db.commit()
    return {"default_broker": body.broker}


@router.delete("/{broker}")
def disconnect_broker(broker: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conn = db.query(BrokerConnection).filter_by(user_id=user.id, broker=broker).first()
    if conn:
        db.delete(conn)
        if user.default_broker == broker:
            user.default_broker = None
        db.commit()
    return {"status": "disconnected"}


@router.get("/tradier/authorize")
def tradier_authorize(
    environment: TradierEnvironment = "sandbox",
    user: User = Depends(get_current_user),
):
    if not can_process_trades(user):
        raise HTTPException(status_code=402, detail="Active subscription required")
    if not settings.tradier_client_id or not settings.tradier_client_secret:
        raise HTTPException(status_code=503, detail="Tradier OAuth is not configured on the server")
    state = create_oauth_state(user.id, f"tradier:{environment}")
    return {"url": TradierAdapter.authorization_url(state, environment)}


@router.post("/tradier/token", response_model=TradierTokenConnectResponse)
async def tradier_connect_token(
    body: TradierTokenConnectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Connect or switch using an individual Tradier API access token."""
    if not can_process_trades(user):
        raise HTTPException(status_code=402, detail="Active subscription required")

    access_token = body.access_token.strip()
    if not access_token:
        raise HTTPException(status_code=400, detail="access_token is required")

    environment = normalize_tradier_environment(body.environment)
    adapter = TradierAdapter(access_token=access_token, environment=environment)
    account_id = (body.account_id or "").strip() or None
    resolved = await adapter.fetch_primary_account_id()
    if not resolved and not account_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Tradier token was rejected or has no account — "
                f"confirm it matches the selected {environment} environment"
            ),
        )
    account_id = account_id or resolved

    creds = pack_credentials(access_token)
    _apply_tradier_trading_prefs(user, environment)
    conn = _upsert_connection(
        db,
        user,
        "tradier",
        credentials=creds,
        account_id=account_id,
        environment=environment,
    )
    return TradierTokenConnectResponse(
        broker="tradier",
        status=conn.status,
        account_id=conn.account_id,
        environment=environment,
    )


@router.get("/tradier/callback")
async def tradier_callback(code: str, state: str, db: Session = Depends(get_db)):
    environment: TradierEnvironment = normalize_tradier_environment(None)
    user_id: str | None = None
    for env in ("sandbox", "live"):
        try:
            user_id = verify_oauth_state(state, f"tradier:{env}")
            environment = env
            break
        except ValueError:
            continue
    if user_id is None:
        try:
            user_id = verify_oauth_state(state, "tradier")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="User not found for OAuth state")

    tokens = await TradierAdapter.exchange_code(code, environment)
    access_token = str(tokens.get("access_token", ""))
    if not access_token:
        raise HTTPException(status_code=400, detail="Tradier did not return an access token")

    adapter = TradierAdapter(access_token=access_token, environment=environment)
    account_id = await adapter.fetch_primary_account_id()
    creds = pack_credentials(access_token)
    _apply_tradier_trading_prefs(user, environment)
    _upsert_connection(
        db,
        user,
        "tradier",
        credentials=creds,
        account_id=account_id,
        environment=environment,
    )
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

    expires_in = tokens.get("expires_in")
    expires_at = time.time() + int(expires_in) if expires_in else None

    schwab = SchwabAdapter(
        access_token=access_token,
        refresh_token=str(tokens.get("refresh_token", "")),
        expires_at=expires_at,
    )

    account_hash = await schwab.fetch_primary_account_hash()
    creds = pack_credentials(
        access_token,
        str(tokens.get("refresh_token", "")),
        int(expires_in) if expires_in else None,
    )
    _upsert_connection(db, user, "schwab", credentials=creds, account_id=account_hash)
    return RedirectResponse(url=oauth_success_redirect("schwab"))


@router.post("/ninjatrader/connect", response_model=NinjaTraderConnectResponse)
def ninjatrader_connect(
    body: NinjaTraderConnectRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not can_process_trades(user):
        raise HTTPException(status_code=402, detail="Active subscription required")

    forward_url = body.forward_url.strip()
    if not forward_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="forward_url must be an HTTPS URL")

    creds = pack_ninjatrader_credentials(forward_url, body.webhook_secret)
    conn = _upsert_connection(
        db,
        user,
        "ninjatrader",
        credentials=creds,
        account_id=(body.account_label or "").strip() or None,
    )
    return NinjaTraderConnectResponse(
        broker="ninjatrader",
        status=conn.status,
        forward_url=forward_url,
    )


@router.post("/{broker}/test-order", response_model=TestOrderResponse)
async def test_broker_order(
    broker: str,
    body: TestOrderRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not can_process_trades(user):
        raise HTTPException(status_code=402, detail="Active subscription required")

    conn = db.query(BrokerConnection).filter_by(user_id=user.id, broker=broker, status="connected").first()
    if conn is None:
        raise HTTPException(status_code=404, detail="Broker not connected")

    mode = user.default_mode
    if mode == "live" and not user.live_trading_enabled:
        raise HTTPException(status_code=400, detail="Live trading is not enabled")

    skip_rth = broker == "ninjatrader" and (
        body.dry_run is True or (body.dry_run is None and mode == "paper")
    )
    if not skip_rth and not market_hours.is_rth():
        raise HTTPException(status_code=400, detail=market_hours.RTH_SKIP_REASON)

    adapter = await get_adapter(db, conn)
    if broker == "ninjatrader":
        if not isinstance(adapter, NinjaTraderAdapter):
            raise HTTPException(status_code=500, detail="Invalid NinjaTrader adapter")
        from app.schemas.trade import ValidatedFuturesTrade

        action = (body.action or body.side or "BUY").upper()
        futures_action = "BUY" if action in {"BUY", "BTO", "BUY_TO_OPEN"} else "SELL"
        symbol = ninjatrader_futures_symbol(body.symbol)
        validated = ValidatedFuturesTrade(
            action=futures_action,
            symbol=symbol,
            quantity=max(1, body.quantity),
            confidence=1.0,
            rationale="broker test order",
            broker="ninjatrader",
            external_id=f"test-{body.symbol.lower()}",
        )
        result = await adapter.execute_futures_order(
            validated,
            mode=mode,
            dry_run=body.dry_run if body.dry_run is not None else mode == "paper",
            user_id=user.id,
        )
    else:
        result = await adapter.place_equity_order(body.symbol.upper(), body.quantity, body.side, mode)
    simulated = bool(result.raw_response.get("simulated"))
    if result.success:
        message = "Connection verified with simulated order" if simulated else "Test order placed successfully"
    else:
        message = result.error or "Test order failed"

    return TestOrderResponse(
        success=result.success,
        broker=broker,
        mode=mode,
        order_id=result.order_id,
        simulated=simulated,
        message=message,
    )

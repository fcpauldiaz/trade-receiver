from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter, OptionContract
from app.brokers.schwab import SchwabAdapter
from app.brokers.tradier import TradierAdapter
from app.brokers.webull import WebullAdapter
from app.models.tables import BrokerConnection
from app.services.broker_credentials import parse_credentials
from app.services.crypto import decrypt_value
from app.services.token_refresh import ensure_fresh_credentials


def _decrypt_creds(value: str | None) -> str:
    if not value:
        return ""
    try:
        return decrypt_value(value)
    except Exception:
        return value


async def get_adapter(db: Session, connection: BrokerConnection) -> BrokerAdapter:
    raw = await ensure_fresh_credentials(db, connection)
    bundle = parse_credentials(raw)
    access_token = bundle.get("access_token", raw)

    if connection.broker == "tradier":
        return TradierAdapter(access_token=access_token, account_id=connection.account_id)
    if connection.broker == "schwab":
        return SchwabAdapter(
            access_token=access_token,
            account_hash=connection.account_id,
            refresh_token=bundle.get("refresh_token"),
            expires_at=bundle.get("expires_at"),
        )
    if connection.broker == "webull":
        return WebullAdapter(access_token=access_token)
    raise ValueError(f"Unsupported broker: {connection.broker}")


def get_adapter_sync(connection: BrokerConnection) -> BrokerAdapter:
    raw = _decrypt_creds(connection.encrypted_credentials)
    bundle = parse_credentials(raw)
    access_token = bundle.get("access_token", raw)
    if connection.broker == "tradier":
        return TradierAdapter(access_token=access_token, account_id=connection.account_id)
    if connection.broker == "schwab":
        return SchwabAdapter(
            access_token=access_token,
            account_hash=connection.account_id,
            refresh_token=bundle.get("refresh_token"),
            expires_at=bundle.get("expires_at"),
        )
    if connection.broker == "webull":
        return WebullAdapter(access_token=access_token)
    raise ValueError(f"Unsupported broker: {connection.broker}")


async def fetch_chain(adapter: BrokerAdapter, underlying: str, expiration: date | None) -> list[OptionContract]:
    return await adapter.get_option_chain(underlying, expiration)

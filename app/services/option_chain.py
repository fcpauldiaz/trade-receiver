from datetime import date
from decimal import Decimal

from app.brokers.base import BrokerAdapter, OptionContract
from app.brokers.schwab import SchwabAdapter
from app.brokers.tradier import TradierAdapter
from app.brokers.webull import WebullAdapter
from app.models.tables import BrokerConnection
from app.services.crypto import decrypt_value


def _decrypt_creds(value: str | None) -> str:
    if not value:
        return ""
    try:
        return decrypt_value(value)
    except Exception:
        return value


def get_adapter(connection: BrokerConnection) -> BrokerAdapter:
    creds = _decrypt_creds(connection.encrypted_credentials)
    if connection.broker == "tradier":
        token = creds or None
        return TradierAdapter(access_token=token, account_id=connection.account_id)
    if connection.broker == "schwab":
        return SchwabAdapter(access_token=creds or None)
    if connection.broker == "webull":
        return WebullAdapter(access_token=creds or None)
    return TradierAdapter()


async def fetch_chain(adapter: BrokerAdapter, underlying: str, expiration: date | None) -> list[OptionContract]:
    return await adapter.get_option_chain(underlying, expiration)

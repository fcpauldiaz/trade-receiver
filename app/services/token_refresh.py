from sqlalchemy.orm import Session

from app.brokers.schwab import SchwabAdapter
from app.brokers.tradier import TradierAdapter
from app.models.tables import BrokerConnection
from app.services.broker_credentials import pack_credentials, parse_credentials
from app.services.crypto import decrypt_value, encrypt_value


def _decrypt_raw(value: str | None) -> str:
    if not value:
        return ""
    try:
        return decrypt_value(value)
    except Exception:
        return value


async def ensure_fresh_credentials(db: Session, connection: BrokerConnection) -> str:
    raw = _decrypt_raw(connection.encrypted_credentials)
    bundle = parse_credentials(raw)
    access_token = bundle.get("access_token", raw)

    if connection.broker == "schwab":
        adapter = SchwabAdapter(
            access_token=access_token,
            account_hash=connection.account_id,
            refresh_token=bundle.get("refresh_token"),
            expires_at=bundle.get("expires_at"),
        )
        if adapter.token_needs_refresh():
            refreshed = await adapter.refresh_access_token()
            if refreshed and refreshed.get("access_token"):
                packed = pack_credentials(
                    str(refreshed["access_token"]),
                    str(refreshed.get("refresh_token") or bundle.get("refresh_token") or ""),
                    int(refreshed.get("expires_in", 1800)),
                )
                connection.encrypted_credentials = encrypt_value(packed)
                db.commit()
                return str(refreshed["access_token"])
    return access_token

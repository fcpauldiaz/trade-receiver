import json
import time
from typing import TypedDict


class BrokerTokenBundle(TypedDict, total=False):
    access_token: str
    refresh_token: str
    expires_at: float


def pack_credentials(access_token: str, refresh_token: str | None = None, expires_in: int | None = None) -> str:
    bundle: BrokerTokenBundle = {"access_token": access_token}
    if refresh_token:
        bundle["refresh_token"] = refresh_token
    if expires_in:
        bundle["expires_at"] = time.time() + expires_in
    return json.dumps(bundle)


def parse_credentials(raw: str) -> BrokerTokenBundle:
    raw = raw.strip()
    if raw.startswith("{"):
        data = json.loads(raw)
        return BrokerTokenBundle(
            access_token=str(data.get("access_token", "")),
            refresh_token=str(data.get("refresh_token", "")) if data.get("refresh_token") else None,
            expires_at=float(data["expires_at"]) if data.get("expires_at") else None,
        )
    return BrokerTokenBundle(access_token=raw)


def access_token_from_raw(raw: str) -> str:
    return parse_credentials(raw).get("access_token", raw)

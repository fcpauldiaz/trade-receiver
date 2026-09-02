import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketState

from app.api.deps import get_current_user
from app.config import settings
from app.database import SessionLocal, get_db
from app.models.tables import User
from app.services.device_bridge import registry, start_device_bridge
from app.services.device_tokens import pair_device, resolve_device_by_token, revoke_device, touch_device_seen
from app.services.entitlements import can_process_trades

logger = logging.getLogger(__name__)

router = APIRouter(tags=["devices"])


class PairDeviceRequest(BaseModel):
    name: str = ""


class PairDeviceResponse(BaseModel):
    device_id: str
    device_token: str
    ws_url: str
    name: str


class DeviceStatus(BaseModel):
    id: str
    name: str
    online: bool
    last_seen_at: str | None
    created_at: str
    revoked: bool


def _ws_url(device_token: str) -> str:
    base = settings.receiver_base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base.removeprefix("https://")
    elif base.startswith("http://"):
        base = "ws://" + base.removeprefix("http://")
    return f"{base}/v1/devices/ws?token={device_token}"


@router.post("/v1/me/devices/pair", response_model=PairDeviceResponse)
def pair_user_device(
    body: PairDeviceRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not can_process_trades(user):
        raise HTTPException(status_code=402, detail="Active subscription required")

    device, device_token = pair_device(db, user_id=user.id, name=body.name)
    return PairDeviceResponse(
        device_id=device.id,
        device_token=device_token,
        ws_url=_ws_url(device_token),
        name=device.name,
    )


@router.get("/v1/me/devices", response_model=list[DeviceStatus])
def list_user_devices(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models.tables import UserDevice

    rows = db.query(UserDevice).filter(UserDevice.user_id == user.id).order_by(UserDevice.created_at.desc()).all()
    return [
        DeviceStatus(
            id=row.id,
            name=row.name,
            online=registry.is_device_online(user.id, row.id),
            last_seen_at=row.last_seen_at.isoformat() if row.last_seen_at else None,
            created_at=row.created_at.isoformat(),
            revoked=row.revoked_at is not None,
        )
        for row in rows
    ]


@router.delete("/v1/me/devices/{device_id}")
def delete_user_device(
    device_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not revoke_device(db, user.id, device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    return {"status": "revoked", "device_id": device_id}


@router.websocket("/v1/devices/ws")
async def device_websocket(
    websocket: WebSocket,
    token: str = Query(default=""),
):
    await start_device_bridge()

    if not token.strip():
        await websocket.close(code=4401, reason="missing device token")
        return

    db = SessionLocal()
    try:
        device = resolve_device_by_token(db, token)
        if device is None:
            await websocket.close(code=4401, reason="invalid or revoked device token")
            return

        user_id = device.user_id
        device_id = device.id
        touch_device_seen(db, device)
    finally:
        db.close()

    await websocket.accept()
    await registry.register(user_id, device_id, websocket)
    logger.info("device connected user=%s device=%s", user_id, device_id)

    try:
        while True:
            raw = await websocket.receive_text()
            handled = await registry.handle_incoming(user_id, device_id, raw)
            if handled:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                await registry.handle_incoming(user_id, device_id, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("device websocket error user=%s device=%s", user_id, device_id)
    finally:
        await registry.unregister(user_id, device_id)
        if websocket.client_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close()
            except Exception:
                pass
        logger.info("device disconnected user=%s device=%s", user_id, device_id)

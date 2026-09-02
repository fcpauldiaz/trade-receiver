from sqlalchemy.orm import Session

from app.models.tables import UserDevice
from app.services.jwt_auth import generate_api_key, hash_api_key


def create_device_token() -> str:
    return f"ntd_{generate_api_key()}"


def pair_device(
    db: Session,
    *,
    user_id: str,
    name: str = "",
) -> tuple[UserDevice, str]:
    device_token = create_device_token()
    device = UserDevice(
        user_id=user_id,
        name=name.strip(),
        token_hash=hash_api_key(device_token),
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return device, device_token


def resolve_device_by_token(db: Session, device_token: str) -> UserDevice | None:
    token_hash = hash_api_key(device_token.strip())
    return (
        db.query(UserDevice)
        .filter(UserDevice.token_hash == token_hash, UserDevice.revoked_at.is_(None))
        .first()
    )


def revoke_device(db: Session, user_id: str, device_id: str) -> bool:
    device = (
        db.query(UserDevice)
        .filter(UserDevice.id == device_id, UserDevice.user_id == user_id)
        .first()
    )
    if device is None:
        return False
    from datetime import datetime, timezone

    device.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return True


def touch_device_seen(db: Session, device: UserDevice) -> None:
    from datetime import datetime, timezone

    device.last_seen_at = datetime.now(timezone.utc)
    db.commit()

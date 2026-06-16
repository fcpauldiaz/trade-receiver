from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.tables import Subscription, User
from app.services.jwt_auth import generate_api_key, hash_api_key

router = APIRouter(prefix="/v1/internal", tags=["internal"])


class ProvisionRequest(BaseModel):
    auth_id: str
    email: EmailStr
    name: str | None = None


class ProvisionResponse(BaseModel):
    user_id: str
    created: bool
    linked: bool


class DeviceTokenRequest(BaseModel):
    auth_id: str | None = None
    email: EmailStr | None = None


class DeviceTokenResponse(BaseModel):
    api_key: str
    ingest_url: str


def _verify_internal_secret(x_internal_secret: str = Header(..., alias="X-Internal-Secret")) -> None:
    if not settings.internal_api_secret or x_internal_secret != settings.internal_api_secret:
        raise HTTPException(status_code=401, detail="Invalid internal secret")


def _resolve_user(db: Session, auth_id: str | None, email: str | None) -> User | None:
    if auth_id:
        user = db.query(User).filter(User.better_auth_id == auth_id).first()
        if user:
            return user
    if email:
        return db.query(User).filter(User.email == email).first()
    return None


@router.post("/provision", response_model=ProvisionResponse, dependencies=[Depends(_verify_internal_secret)])
def provision_user(body: ProvisionRequest, db: Session = Depends(get_db)):
    by_auth = db.query(User).filter(User.better_auth_id == body.auth_id).first()
    if by_auth:
        return ProvisionResponse(user_id=by_auth.id, created=False, linked=False)

    by_email = db.query(User).filter(User.email == body.email).first()
    if by_email:
        if by_email.better_auth_id and by_email.better_auth_id != body.auth_id:
            raise HTTPException(status_code=409, detail="Email linked to another auth account")
        by_email.better_auth_id = body.auth_id
        if body.name and not by_email.name:
            by_email.name = body.name
        db.commit()
        return ProvisionResponse(user_id=by_email.id, created=False, linked=True)

    user = User(
        email=body.email,
        name=body.name,
        better_auth_id=body.auth_id,
        api_key_hash=hash_api_key(generate_api_key()),
    )
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id, status="none", plan_name="free"))
    db.commit()
    db.refresh(user)
    return ProvisionResponse(user_id=user.id, created=True, linked=False)


@router.post("/device-token", response_model=DeviceTokenResponse, dependencies=[Depends(_verify_internal_secret)])
def issue_device_token(body: DeviceTokenRequest, db: Session = Depends(get_db)):
    if not body.auth_id and not body.email:
        raise HTTPException(status_code=400, detail="auth_id or email required")
    user = _resolve_user(db, body.auth_id, body.email)
    if user is None:
        raise HTTPException(status_code=404, detail="User not provisioned")
    api_key = generate_api_key()
    user.api_key_hash = hash_api_key(api_key)
    db.commit()
    base = settings.receiver_base_url.rstrip("/")
    path = settings.ingest_path if settings.ingest_path.startswith("/") else f"/{settings.ingest_path}"
    return DeviceTokenResponse(api_key=api_key, ingest_url=f"{base}{path}")

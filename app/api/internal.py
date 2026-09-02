from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.tables import Subscription, User
from app.services.desktop_assets import save_asset
from app.services.entitlements import can_process_trades, grant_subscription
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


class GrantSubscriptionRequest(BaseModel):
    auth_id: str | None = None
    email: EmailStr | None = None
    status: str = Field(default="active", min_length=1, max_length=32)
    plan_name: str = Field(default="pro", min_length=1, max_length=64)


class GrantSubscriptionResponse(BaseModel):
    user_id: str
    status: str
    plan_name: str
    can_process_trades: bool


def _verify_internal_secret(x_internal_secret: str = Header(..., alias="X-Internal-Secret")) -> None:
    if not settings.internal_api_secret or x_internal_secret != settings.internal_api_secret:
        raise HTTPException(status_code=401, detail="Invalid internal secret")


def _resolve_user(db: Session, auth_id: str | None, email: str | None) -> User | None:
    if auth_id:
        user = db.get(User, auth_id)
        if user:
            return user
    if email:
        return db.query(User).filter(User.email == email).first()
    return None


def _ensure_subscription(db: Session, user: User) -> None:
    if user.subscription is None:
        db.add(Subscription(user_id=user.id, status="none", plan_name="free"))


@router.post("/provision", response_model=ProvisionResponse, dependencies=[Depends(_verify_internal_secret)])
def provision_user(body: ProvisionRequest, db: Session = Depends(get_db)):
    by_id = db.get(User, body.auth_id)
    if by_id:
        _ensure_subscription(db, by_id)
        if body.name and not by_id.name:
            by_id.name = body.name
        db.commit()
        return ProvisionResponse(user_id=by_id.id, created=False, linked=False)

    by_email = db.query(User).filter(User.email == body.email).first()
    if by_email:
        _ensure_subscription(db, by_email)
        if body.name and not by_email.name:
            by_email.name = body.name
        db.commit()
        return ProvisionResponse(user_id=by_email.id, created=False, linked=True)

    user = User(
        id=body.auth_id,
        email=body.email,
        name=body.name,
        api_key_hash=hash_api_key(generate_api_key()),
    )
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id, status="none", plan_name="free"))
    db.commit()
    db.refresh(user)
    return ProvisionResponse(user_id=user.id, created=True, linked=False)


@router.post(
    "/subscription/grant",
    response_model=GrantSubscriptionResponse,
    dependencies=[Depends(_verify_internal_secret)],
)
def grant_user_subscription(body: GrantSubscriptionRequest, db: Session = Depends(get_db)):
    if not body.auth_id and not body.email:
        raise HTTPException(status_code=400, detail="auth_id or email required")
    user = _resolve_user(db, body.auth_id, body.email)
    if user is None:
        raise HTTPException(status_code=404, detail="User not provisioned")
    sub = grant_subscription(
        db,
        user,
        status=body.status,
        plan_name=body.plan_name,
        revoke_device=body.status not in {"active", "trialing"},
    )
    db.refresh(user)
    return GrantSubscriptionResponse(
        user_id=user.id,
        status=sub.status,
        plan_name=sub.plan_name,
        can_process_trades=can_process_trades(user),
    )


@router.post("/device-token", response_model=DeviceTokenResponse, dependencies=[Depends(_verify_internal_secret)])
def issue_device_token(body: DeviceTokenRequest, db: Session = Depends(get_db)):
    if not body.auth_id and not body.email:
        raise HTTPException(status_code=400, detail="auth_id or email required")
    user = _resolve_user(db, body.auth_id, body.email)
    if user is None:
        if not body.auth_id or not body.email:
            raise HTTPException(status_code=404, detail="User not provisioned")
        user = User(
            id=body.auth_id,
            email=body.email,
            api_key_hash=hash_api_key(generate_api_key()),
        )
        db.add(user)
        db.flush()
        _ensure_subscription(db, user)
        db.commit()
        db.refresh(user)
    api_key = generate_api_key()
    user.api_key_hash = hash_api_key(api_key)
    db.commit()
    base = settings.receiver_base_url.rstrip("/")
    path = settings.ingest_path if settings.ingest_path.startswith("/") else f"/{settings.ingest_path}"
    return DeviceTokenResponse(api_key=api_key, ingest_url=f"{base}{path}")


class DesktopAssetsResponse(BaseModel):
    saved: list[str]


@router.post(
    "/desktop/assets",
    response_model=DesktopAssetsResponse,
    dependencies=[Depends(_verify_internal_secret)],
)
async def upload_desktop_assets(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files")
    saved: list[str] = []
    for upload in files:
        name = upload.filename or ""
        data = await upload.read()
        try:
            saved.extend(save_asset(name, data))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DesktopAssetsResponse(saved=saved)

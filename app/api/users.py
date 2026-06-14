import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.database import get_db
from app.models.tables import Subscription, User
from app.services.entitlements import can_process_trades, ensure_webhook_secret

router = APIRouter(prefix="/v1", tags=["users"])


class UserCreate(BaseModel):
    email: EmailStr
    name: str | None = None


class UserResponse(BaseModel):
    id: str
    email: str
    name: str | None
    api_key: str
    webhook_url: str | None
    can_process_trades: bool


class WebhookInfo(BaseModel):
    url: str | None
    enabled: bool


@router.post("/users", response_model=UserResponse)
def create_user(body: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    api_key = secrets.token_urlsafe(32)
    user = User(
        email=body.email,
        name=body.name,
        api_key_hash=hashlib.sha256(api_key.encode()).hexdigest(),
    )
    db.add(user)
    db.flush()
    sub = Subscription(user_id=user.id, status="none", plan_name="free")
    db.add(sub)
    db.commit()
    db.refresh(user)
    return _user_response(user, api_key)


@router.get("/me", response_model=UserResponse)
def get_me(user: User = Depends(get_current_user)):
    return _user_response(user, api_key="***")


@router.get("/me/webhook", response_model=WebhookInfo)
def get_webhook(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not can_process_trades(user):
        return WebhookInfo(url=None, enabled=False)
    secret = ensure_webhook_secret(user)
    db.commit()
    url = f"{settings.receiver_base_url}/hooks/{user.id}/{secret}"
    return WebhookInfo(url=url, enabled=user.webhook_enabled)


def _user_response(user: User, api_key: str) -> UserResponse:
    webhook_url = None
    if user.webhook_enabled and user.webhook_secret:
        webhook_url = f"{settings.receiver_base_url}/hooks/{user.id}/{user.webhook_secret}"
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        api_key=api_key,
        webhook_url=webhook_url,
        can_process_trades=can_process_trades(user),
    )

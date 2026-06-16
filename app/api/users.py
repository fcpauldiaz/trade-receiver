import hashlib
import secrets

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.database import get_db
from app.models.tables import User
from app.services.entitlements import can_process_trades, ensure_webhook_secret
from app.services.jwt_auth import generate_api_key, hash_api_key

router = APIRouter(prefix="/v1", tags=["users"])


class UserResponse(BaseModel):
    id: str
    email: str
    name: str | None
    api_key: str | None = None
    webhook_url: str | None
    can_process_trades: bool
    onboarding_completed: bool


class WebhookInfo(BaseModel):
    url: str | None
    enabled: bool


@router.get("/me", response_model=UserResponse)
def get_me(user: User = Depends(get_current_user)):
    return _user_response(user)


@router.post("/me/regenerate-api-key", response_model=UserResponse)
def regenerate_api_key(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    api_key = generate_api_key()
    user.api_key_hash = hash_api_key(api_key)
    db.commit()
    return _user_response(user, api_key=api_key)


@router.get("/me/webhook", response_model=WebhookInfo)
def get_webhook(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not can_process_trades(user):
        return WebhookInfo(url=None, enabled=False)
    secret = ensure_webhook_secret(user)
    db.commit()
    url = f"{settings.receiver_base_url}/hooks/{user.id}/{secret}"
    return WebhookInfo(url=url, enabled=user.webhook_enabled)


def _user_response(user: User, api_key: str | None = None) -> UserResponse:
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
        onboarding_completed=user.onboarding_completed,
    )

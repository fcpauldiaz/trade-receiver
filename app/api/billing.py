import hashlib
import hmac
import json
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.database import get_db
from app.models.tables import User
from app.services.entitlements import (
    can_process_trades,
    ensure_webhook_secret,
    upsert_subscription_from_lemon,
    verify_lemon_squeezy_signature,
)

router = APIRouter(prefix="/v1", tags=["billing"])


class BillingStatus(BaseModel):
    status: str
    plan_name: str
    renews_at: datetime | None
    ends_at: datetime | None
    can_process_trades: bool
    webhook_enabled: bool


@router.get("/me/billing", response_model=BillingStatus)
def get_billing(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sub = user.subscription
    return BillingStatus(
        status=sub.status if sub else "none",
        plan_name=sub.plan_name if sub else "free",
        renews_at=sub.renews_at if sub else None,
        ends_at=sub.ends_at if sub else None,
        can_process_trades=can_process_trades(user),
        webhook_enabled=user.webhook_enabled,
    )


@router.post("/me/billing/regenerate-webhook")
def regenerate_webhook(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not can_process_trades(user):
        raise HTTPException(status_code=402, detail="Active subscription required")
    user.webhook_secret = secrets.token_urlsafe(32)
    user.webhook_enabled = True
    db.commit()
    return {"webhook_secret": user.webhook_secret}


@router.post("/webhooks/lemon-squeezy")
async def lemon_squeezy_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_signature: str | None = Header(default=None, alias="X-Signature"),
):
    body = await request.body()
    secret = settings.lemon_squeezy_webhook_secret or ""
    if secret and not verify_lemon_squeezy_signature(body, x_signature or "", secret):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body)
    event_name = payload.get("meta", {}).get("event_name", "")
    attrs = payload.get("data", {}).get("attributes", {})
    custom = payload.get("meta", {}).get("custom_data", {}) or {}
    user_id = custom.get("user_id")
    email = attrs.get("user_email") or custom.get("email")

    user: User | None = None
    if user_id:
        user = db.get(User, user_id)
    elif email:
        user = db.query(User).filter(User.email == email).first()
    if user is None:
        return {"ok": True, "skipped": "user not found"}

    status_map = {
        "subscription_created": "active",
        "subscription_updated": attrs.get("status", "active"),
        "subscription_payment_success": "active",
        "subscription_payment_failed": "past_due",
        "subscription_cancelled": "cancelled",
        "subscription_expired": "expired",
    }
    status = status_map.get(event_name, attrs.get("status", "none"))
    renews_at = _parse_dt(attrs.get("renews_at"))
    ends_at = _parse_dt(attrs.get("ends_at"))

    upsert_subscription_from_lemon(
        db,
        user,
        customer_id=str(attrs.get("customer_id", "")),
        subscription_id=str(payload.get("data", {}).get("id", "")),
        variant_id=str(attrs.get("variant_id", "")),
        status=status,
        plan_name=attrs.get("variant_name", "pro"),
        renews_at=renews_at,
        ends_at=ends_at,
    )
    if status == "active":
        ensure_webhook_secret(user)
        db.commit()
    return {"ok": True}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None

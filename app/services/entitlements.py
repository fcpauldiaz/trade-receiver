import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.tables import Subscription, User


def _in_cancelled_grace(sub: Subscription) -> bool:
    if sub.ends_at is None:
        return False
    ends = sub.ends_at.replace(tzinfo=timezone.utc) if sub.ends_at.tzinfo is None else sub.ends_at
    return ends > datetime.now(timezone.utc)


def can_process_trades(user: User) -> bool:
    sub = user.subscription
    if sub is None or not user.webhook_enabled:
        return False
    if sub.status == "active":
        return True
    if sub.status == "cancelled" and _in_cancelled_grace(sub):
        return True
    return False


def require_active_subscription(user: User) -> tuple[bool, str]:
    if not can_process_trades(user):
        return False, "Active subscription required"
    return True, ""


def ensure_webhook_secret(user: User) -> str:
    if not user.webhook_secret:
        user.webhook_secret = secrets.token_urlsafe(32)
    return user.webhook_secret


def disable_webhook_on_lapse(db: Session, user: User) -> None:
    user.webhook_enabled = False
    user.webhook_secret = secrets.token_urlsafe(32)
    db.commit()


def verify_lemon_squeezy_signature(payload: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def upsert_subscription_from_lemon(
    db: Session,
    user: User,
    *,
    customer_id: str | None,
    subscription_id: str | None,
    variant_id: str | None,
    status: str,
    plan_name: str = "pro",
    renews_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> Subscription:
    sub = user.subscription
    if sub is None:
        sub = Subscription(user_id=user.id)
        db.add(sub)
    sub.lemon_squeezy_customer_id = customer_id
    sub.lemon_squeezy_subscription_id = subscription_id
    sub.variant_id = variant_id
    sub.status = status
    sub.plan_name = plan_name
    sub.renews_at = renews_at
    sub.ends_at = ends_at
    if status == "active":
        user.webhook_enabled = True
        ensure_webhook_secret(user)
    elif status == "cancelled" and ends_at and ends_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
        ensure_webhook_secret(user)
    elif status in ("expired", "past_due"):
        disable_webhook_on_lapse(db, user)
    elif status == "cancelled":
        disable_webhook_on_lapse(db, user)
    db.commit()
    db.refresh(sub)
    return sub

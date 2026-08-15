import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import Subscription, User

ACTIVE_STATUSES = {"active", "trialing"}
REVOKE_STATUSES = {"expired", "past_due", "paused", "canceled", "cancelled"}


def _in_cancelled_grace(sub: Subscription) -> bool:
    if sub.ends_at is None:
        return False
    ends = sub.ends_at.replace(tzinfo=timezone.utc) if sub.ends_at.tzinfo is None else sub.ends_at
    return ends > datetime.now(timezone.utc)


def can_process_trades(user: User) -> bool:
    sub = user.subscription
    if sub is None:
        return False
    if sub.status in ACTIVE_STATUSES:
        return True
    if sub.status in {"cancelled", "canceled", "scheduled_cancel"} and _in_cancelled_grace(sub):
        return True
    return False


def require_active_subscription(user: User) -> tuple[bool, str]:
    if not can_process_trades(user):
        return False, "Active subscription required"
    return True, ""


def revoke_device_access(db: Session, user: User) -> None:
    user.api_key_hash = None
    db.commit()


def verify_creem_signature(payload: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


def upsert_subscription_from_creem(
    db: Session,
    user: User,
    *,
    customer_id: str | None,
    subscription_id: str | None,
    product_id: str | None,
    status: str,
    plan_name: str = "pro",
    renews_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> Subscription:
    sub = user.subscription
    if sub is None:
        sub = Subscription(user_id=user.id)
        db.add(sub)
    if customer_id is not None:
        sub.creem_customer_id = customer_id
    if subscription_id is not None:
        sub.creem_subscription_id = subscription_id
    if product_id is not None:
        sub.variant_id = product_id
    sub.status = status
    sub.plan_name = plan_name
    sub.renews_at = renews_at
    sub.ends_at = ends_at
    _maybe_revoke(db, user, status, ends_at)
    db.commit()
    db.refresh(sub)
    return sub


def _maybe_revoke(db: Session, user: User, status: str, ends_at: datetime | None) -> None:
    normalized = "cancelled" if status == "canceled" else status
    if normalized not in REVOKE_STATUSES and normalized != "scheduled_cancel":
        return
    if normalized in {"cancelled", "scheduled_cancel"} and ends_at:
        ends = ends_at.replace(tzinfo=timezone.utc) if ends_at.tzinfo is None else ends_at
        if ends > datetime.now(timezone.utc):
            return
    revoke_device_access(db, user)


def creem_status_from_event(event_type: str, object_status: str | None = None) -> str:
    mapping = {
        "subscription.active": "active",
        "subscription.paid": "active",
        "subscription.trialing": "trialing",
        "subscription.past_due": "past_due",
        "subscription.expired": "expired",
        "subscription.paused": "paused",
        "subscription.canceled": "cancelled",
        "subscription.scheduled_cancel": "scheduled_cancel",
        "subscription.update": object_status or "active",
        "checkout.completed": "active",
    }
    status = mapping.get(event_type, object_status or "none")
    if status == "canceled":
        return "cancelled"
    return status


def extract_creem_ids(obj: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    customer = obj.get("customer")
    product = obj.get("product")
    subscription = obj.get("subscription")

    customer_id = _id_of(customer) or _as_str(obj.get("customer_id"))
    product_id = _id_of(product) or _as_str(obj.get("product_id")) or _as_str(obj.get("product"))
    subscription_id = _id_of(obj if obj.get("object") == "subscription" else None)
    if subscription_id is None:
        subscription_id = _id_of(subscription) or _as_str(
            obj.get("id") if obj.get("object") == "subscription" else None
        )
    if subscription_id is None and isinstance(subscription, str):
        subscription_id = subscription
    return customer_id, subscription_id, product_id


def extract_creem_user_ref(
    obj: dict[str, Any], metadata: dict[str, Any] | None = None
) -> tuple[str | None, str | None]:
    meta = metadata if metadata is not None else obj.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    user_id = meta.get("user_id") or meta.get("referenceId") or meta.get("reference_id")
    email = None
    customer = obj.get("customer")
    if isinstance(customer, dict):
        email = customer.get("email")
    return _as_str(user_id), _as_str(email)


def _id_of(value: Any) -> str | None:
    if isinstance(value, dict):
        return _as_str(value.get("id"))
    if isinstance(value, str):
        return value
    return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

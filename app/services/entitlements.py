from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.tables import Subscription, User

ACTIVE_STATUSES = {"active", "trialing"}


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


def grant_subscription(
    db: Session,
    user: User,
    *,
    status: str = "active",
    plan_name: str = "pro",
    renews_at: datetime | None = None,
    ends_at: datetime | None = None,
    revoke_device: bool = False,
) -> Subscription:
    sub = user.subscription
    if sub is None:
        sub = Subscription(user_id=user.id)
        db.add(sub)
    sub.status = status
    sub.plan_name = plan_name
    sub.renews_at = renews_at
    sub.ends_at = ends_at
    if revoke_device or not can_process_trades(user):
        user.api_key_hash = None
    db.commit()
    db.refresh(sub)
    return sub

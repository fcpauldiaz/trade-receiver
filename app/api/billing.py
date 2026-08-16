import json
import logging
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.database import get_db
from app.models.tables import ProcessedWebhookEvent, User
from app.services.creem import CreemError, create_checkout, create_customer_portal, get_checkout, product_id_for_plan
from app.services.entitlements import (
    can_process_trades,
    creem_status_from_event,
    extract_creem_ids,
    extract_creem_user_ref,
    upsert_subscription_from_creem,
    verify_creem_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["billing"])

_ACTIVE_CHECKOUT_STATUSES = {"active", "trialing"}


class BillingStatus(BaseModel):
    status: str
    plan_name: str
    renews_at: datetime | None
    ends_at: datetime | None
    can_process_trades: bool
    customer_portal_url: str | None = None


class CheckoutRequest(BaseModel):
    plan: Literal["monthly", "yearly"] = "monthly"


class CheckoutResponse(BaseModel):
    checkout_url: str
    checkout_id: str | None = None


class ConfirmCheckoutRequest(BaseModel):
    checkout_id: str = Field(min_length=8, max_length=128)


@router.get("/me/billing", response_model=BillingStatus)
def get_billing(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _billing_status(user)


@router.post("/me/billing/checkout", response_model=CheckoutResponse)
def create_billing_checkout(
    user: User = Depends(get_current_user),
    body: CheckoutRequest | None = None,
):
    if not settings.creem_api_key:
        raise HTTPException(status_code=503, detail="CREEM_API_KEY is not configured")
    plan = body.plan if body else "monthly"
    product_id = product_id_for_plan(plan)
    if not product_id:
        missing = "CREEM_YEARLY_PRODUCT_ID" if plan == "yearly" else "CREEM_PRODUCT_ID"
        raise HTTPException(status_code=503, detail=f"{missing} is not configured")

    success_url = settings.creem_success_url or f"{settings.platform_base_url.rstrip('/')}/billing"
    try:
        checkout = create_checkout(
            product_id=product_id,
            success_url=success_url,
            customer_email=user.email,
            metadata={"user_id": user.id, "referenceId": user.id, "plan": plan},
        )
    except CreemError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    checkout_url = checkout.get("checkout_url") or checkout.get("checkoutUrl")
    if not checkout_url:
        raise HTTPException(status_code=502, detail="Creem checkout response missing checkout_url")
    return CheckoutResponse(
        checkout_url=str(checkout_url),
        checkout_id=str(checkout.get("id")) if checkout.get("id") else None,
    )


@router.post("/me/billing/confirm", response_model=BillingStatus)
def confirm_billing_checkout(
    body: ConfirmCheckoutRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not settings.creem_api_key:
        raise HTTPException(status_code=503, detail="CREEM_API_KEY is not configured")
    try:
        checkout = get_checkout(body.checkout_id.strip())
    except CreemError as exc:
        status = 404 if exc.status_code == 404 else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    if not _checkout_matches_user(checkout, user):
        raise HTTPException(status_code=403, detail="Checkout does not match this account")
    if not _checkout_is_paid(checkout):
        raise HTTPException(status_code=409, detail="Checkout is not paid yet")

    _apply_creem_object(db, user, checkout, "checkout.completed")
    db.refresh(user)
    return _billing_status(user)


@router.post("/me/billing/portal")
def create_billing_portal(user: User = Depends(get_current_user)):
    customer_id = user.subscription.creem_customer_id if user.subscription else None
    if not customer_id:
        raise HTTPException(status_code=404, detail="No Creem customer on file")
    try:
        portal = create_customer_portal(customer_id)
    except CreemError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    url = portal.get("customer_portal_link") or portal.get("url") or portal.get("portal_url")
    if not url:
        raise HTTPException(status_code=502, detail="Creem portal response missing URL")
    return {"url": url}


@router.post("/webhooks/creem")
async def creem_webhook(
    request: Request,
    db: Session = Depends(get_db),
    creem_signature: str | None = Header(default=None, alias="creem-signature"),
):
    body = await request.body()
    secret = settings.creem_webhook_secret or ""
    if secret and not verify_creem_signature(body, creem_signature or "", secret):
        logger.warning("creem webhook rejected: invalid signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body)
    event_id = str(payload.get("id") or "")
    if event_id:
        seen = db.query(ProcessedWebhookEvent).filter_by(event_id=event_id).first()
        if seen:
            return {"ok": True, "duplicate": True}

    event_type = str(payload.get("eventType") or payload.get("event_type") or "")
    obj = payload.get("object") or {}
    if not isinstance(obj, dict):
        logger.warning("creem webhook skipped: invalid object event=%s", event_type)
        return {"ok": True, "skipped": "invalid object"}

    metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    user_id, email = extract_creem_user_ref(obj, metadata)
    user = _resolve_user(db, user_id=user_id, email=email)
    if user is None:
        logger.warning("creem webhook skipped: user not found event=%s user_id=%s", event_type, user_id)
        return {"ok": True, "skipped": "user not found"}

    if event_type in {
        "subscription.paid",
        "subscription.active",
        "subscription.trialing",
        "subscription.past_due",
        "subscription.expired",
        "subscription.paused",
        "subscription.canceled",
        "subscription.scheduled_cancel",
        "subscription.update",
        "checkout.completed",
    }:
        _apply_creem_object(db, user, obj, event_type)
        logger.info("creem webhook applied event=%s user=%s", event_type, user.id)

    if event_id:
        db.add(ProcessedWebhookEvent(source="creem", event_id=event_id))
        db.commit()
    return {"ok": True}


def _billing_status(user: User) -> BillingStatus:
    sub = user.subscription
    return BillingStatus(
        status=sub.status if sub else "none",
        plan_name=sub.plan_name if sub else "free",
        renews_at=sub.renews_at if sub else None,
        ends_at=sub.ends_at if sub else None,
        can_process_trades=can_process_trades(user),
        customer_portal_url=None,
    )


def _apply_creem_object(db: Session, user: User, obj: dict[str, Any], event_type: str) -> None:
    customer_id, subscription_id, product_id = extract_creem_ids(obj)
    object_status = _object_status(obj)
    status = creem_status_from_event(event_type, object_status)
    renews_at, ends_at = _period_dates(obj)
    upsert_subscription_from_creem(
        db,
        user,
        customer_id=customer_id,
        subscription_id=subscription_id,
        product_id=product_id,
        status=status,
        plan_name=_plan_name(obj),
        renews_at=renews_at,
        ends_at=ends_at if status in {"cancelled", "scheduled_cancel", "expired"} else None,
    )


def _checkout_matches_user(checkout: dict[str, Any], user: User) -> bool:
    user_id, email = extract_creem_user_ref(checkout)
    if user_id and user_id == user.id:
        return True
    return bool(email and email.lower() == user.email.lower())


def _checkout_is_paid(checkout: dict[str, Any]) -> bool:
    order = checkout.get("order")
    if isinstance(order, dict) and str(order.get("status") or "").lower() == "paid":
        _, subscription_id, _ = extract_creem_ids(checkout)
        return bool(subscription_id)
    status = str(_object_status(checkout) or "").lower()
    if status == "completed":
        if isinstance(order, dict) and str(order.get("status") or "").lower() != "paid":
            return False
        _, subscription_id, _ = extract_creem_ids(checkout)
        return bool(subscription_id)
    return status in _ACTIVE_CHECKOUT_STATUSES


def _object_status(obj: dict[str, Any]) -> str | None:
    if obj.get("status"):
        return str(obj["status"])
    subscription = obj.get("subscription")
    if isinstance(subscription, dict) and subscription.get("status"):
        return str(subscription["status"])
    order = obj.get("order")
    if isinstance(order, dict) and order.get("status"):
        return str(order["status"])
    return None


def _period_dates(obj: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    nested = obj.get("subscription")
    subscription: dict[str, Any] = nested if isinstance(nested, dict) else {}
    renews_at = _parse_dt(
        obj.get("next_transaction_date")
        or subscription.get("next_transaction_date")
        or obj.get("current_period_end_date")
        or subscription.get("current_period_end_date")
    )
    ends_at = _parse_dt(
        obj.get("current_period_end_date")
        or subscription.get("current_period_end_date")
        or obj.get("canceled_at")
        or subscription.get("canceled_at")
    )
    return renews_at, ends_at


def _resolve_user(db: Session, *, user_id: str | None, email: str | None) -> User | None:
    if user_id:
        user = db.get(User, user_id)
        if user is not None:
            return user
    if email:
        return db.query(User).filter(User.email == email).first()
    return None


def _plan_name(obj: dict[str, Any]) -> str:
    product = obj.get("product")
    if isinstance(product, dict) and product.get("name"):
        return str(product["name"])
    subscription = obj.get("subscription")
    if isinstance(subscription, dict):
        product = subscription.get("product")
        if isinstance(product, dict) and product.get("name"):
            return str(product["name"])
    return "pro"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None

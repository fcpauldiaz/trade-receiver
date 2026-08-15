import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.database import get_db
from app.models.tables import ProcessedWebhookEvent, User
from app.services.creem import CreemError, create_checkout, create_customer_portal
from app.services.entitlements import (
    can_process_trades,
    creem_status_from_event,
    extract_creem_ids,
    extract_creem_user_ref,
    upsert_subscription_from_creem,
    verify_creem_signature,
)

router = APIRouter(prefix="/v1", tags=["billing"])


class BillingStatus(BaseModel):
    status: str
    plan_name: str
    renews_at: datetime | None
    ends_at: datetime | None
    can_process_trades: bool
    customer_portal_url: str | None = None


class CheckoutResponse(BaseModel):
    checkout_url: str
    checkout_id: str | None = None


@router.get("/me/billing", response_model=BillingStatus)
def get_billing(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sub = user.subscription
    return BillingStatus(
        status=sub.status if sub else "none",
        plan_name=sub.plan_name if sub else "free",
        renews_at=sub.renews_at if sub else None,
        ends_at=sub.ends_at if sub else None,
        can_process_trades=can_process_trades(user),
        customer_portal_url=None,
    )


@router.post("/me/billing/checkout", response_model=CheckoutResponse)
def create_billing_checkout(user: User = Depends(get_current_user)):
    if not settings.creem_api_key:
        raise HTTPException(status_code=503, detail="CREEM_API_KEY is not configured")
    if not settings.creem_product_id:
        raise HTTPException(status_code=503, detail="CREEM_PRODUCT_ID is not configured")

    success_url = settings.creem_success_url or f"{settings.platform_base_url.rstrip('/')}/billing"
    try:
        checkout = create_checkout(
            product_id=settings.creem_product_id,
            success_url=success_url,
            customer_email=user.email,
            metadata={"user_id": user.id, "referenceId": user.id},
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
        return {"ok": True, "skipped": "invalid object"}

    metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    user_id, email = extract_creem_user_ref(obj, metadata)
    user = _resolve_user(db, user_id=user_id, email=email)
    if user is None:
        return {"ok": True, "skipped": "user not found"}

    customer_id, subscription_id, product_id = extract_creem_ids(obj)
    object_status = str(obj.get("status") or "") if obj.get("status") else None
    status = creem_status_from_event(event_type, object_status)
    plan_name = _plan_name(obj)
    renews_at = _parse_dt(obj.get("next_transaction_date") or obj.get("current_period_end_date"))
    ends_at = _parse_dt(obj.get("current_period_end_date") or obj.get("canceled_at"))

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
        upsert_subscription_from_creem(
            db,
            user,
            customer_id=customer_id,
            subscription_id=subscription_id,
            product_id=product_id,
            status=status,
            plan_name=plan_name,
            renews_at=renews_at,
            ends_at=ends_at if status in {"cancelled", "scheduled_cancel", "expired"} else None,
        )

    if event_id:
        db.add(ProcessedWebhookEvent(source="creem", event_id=event_id))
        db.commit()
    return {"ok": True}


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
    return "pro"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None

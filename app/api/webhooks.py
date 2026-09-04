from datetime import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user
from app.config import settings
from app.database import get_db
from app.models.tables import InboundAlert, InboundWebhook, User
from app.services.ingest_gate import ingest_processing_slot
from app.services.ingest_pipeline import (
    duplicate_response,
    process_inbound_alert,
    resolve_idempotency_key,
)
from app.services.webhook_ingest_audit import (
    safe_record_webhook_ingest_event,
    serialize_webhook_payload,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


class WebhookSummary(BaseModel):
    id: str
    name: str
    enabled: bool
    url: str
    created_at: datetime
    updated_at: datetime


class CreateWebhookRequest(BaseModel):
    name: str = Field(default="", max_length=128)


def _webhook_url(webhook_id: str) -> str:
    base = settings.receiver_base_url.rstrip("/")
    return f"{base}/v1/webhooks/{webhook_id}"


def _to_summary(row: InboundWebhook) -> WebhookSummary:
    return WebhookSummary(
        id=row.id,
        name=row.name,
        enabled=row.enabled,
        url=_webhook_url(row.id),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/v1/me/webhooks", response_model=list[WebhookSummary])
def list_webhooks(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(InboundWebhook)
        .filter(InboundWebhook.user_id == user.id)
        .order_by(InboundWebhook.created_at.desc())
        .all()
    )
    return [_to_summary(row) for row in rows]


@router.post("/v1/me/webhooks", response_model=WebhookSummary)
def create_webhook(
    body: CreateWebhookRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = InboundWebhook(
        user_id=user.id,
        name=body.name.strip(),
        enabled=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_summary(row)


@router.get("/v1/me/webhooks/{webhook_id}", response_model=WebhookSummary)
def get_webhook(
    webhook_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(InboundWebhook).filter_by(id=webhook_id, user_id=user.id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return _to_summary(row)


@router.delete("/v1/me/webhooks/{webhook_id}")
def delete_webhook(
    webhook_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(InboundWebhook).filter_by(id=webhook_id, user_id=user.id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    row.enabled = False
    db.delete(row)
    db.commit()
    return {"status": "deleted", "id": webhook_id}


@router.post("/v1/webhooks/{webhook_id}")
async def receive_webhook(
    webhook_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    row = db.query(InboundWebhook).filter_by(id=webhook_id).first()
    if row is None or not row.enabled:
        raise HTTPException(status_code=404, detail="Webhook not found")

    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Webhook owner not found")

    body = await request.json()
    source = client_ip(request)
    logger.info(
        "webhook ingest received webhook_id=%s user_id=%s source_ip=%s payload=%s",
        webhook_id,
        user.id,
        source or "-",
        serialize_webhook_payload(body),
    )
    async with ingest_processing_slot(user.id):
        try:
            result = await process_inbound_alert(db, user, body, webhook_id=webhook_id)
        except IntegrityError:
            db.rollback()
            result = duplicate_response(db, user, body, webhook_id=webhook_id)
        except HTTPException as exc:
            if exc.status_code == 402:
                key = resolve_idempotency_key(user, body, webhook_id)
                alert = (
                    db.query(InboundAlert)
                    .filter_by(user_id=user.id, idempotency_key=key)
                    .first()
                )
                inactive_result = {
                    "status": "subscription_inactive",
                    "alert_id": alert.id if alert is not None else None,
                    "detail": exc.detail,
                }
                logger.info(
                    "webhook ingest completed webhook_id=%s user_id=%s status=%s alert_id=%s",
                    webhook_id,
                    user.id,
                    inactive_result["status"],
                    inactive_result["alert_id"],
                )
                safe_record_webhook_ingest_event(
                    db,
                    user_id=user.id,
                    inbound_webhook_id=webhook_id,
                    body=body,
                    result=inactive_result,
                )
            raise
        except Exception as exc:
            logger.exception(
                "webhook processing failed webhook_id=%s user_id=%s payload=%s",
                webhook_id,
                user.id,
                serialize_webhook_payload(body),
            )
            db.rollback()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    if "alert_id" not in result:
        key = resolve_idempotency_key(user, body, webhook_id)
        alert = db.query(InboundAlert).filter_by(user_id=user.id, idempotency_key=key).first()
        if alert is not None:
            result = {**result, "alert_id": alert.id}

    logger.info(
        "webhook ingest completed webhook_id=%s user_id=%s status=%s alert_id=%s",
        webhook_id,
        user.id,
        result.get("status"),
        result.get("alert_id"),
    )
    safe_record_webhook_ingest_event(
        db,
        user_id=user.id,
        inbound_webhook_id=webhook_id,
        body=body,
        result=result,
    )
    return result

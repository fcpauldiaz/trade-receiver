from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.database import get_db
from app.models.tables import InboundWebhook, User
from app.services.ingest_gate import ingest_processing_slot
from app.services.ingest_pipeline import duplicate_response, process_inbound_alert

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
    async with ingest_processing_slot(user.id):
        try:
            return await process_inbound_alert(db, user, body, webhook_id=webhook_id)
        except IntegrityError:
            db.rollback()
            return duplicate_response(db, user, body, webhook_id=webhook_id)

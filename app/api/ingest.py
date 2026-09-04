import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user
from app.database import get_db
from app.models.tables import User
from app.services.ingest_gate import ingest_processing_slot
from app.services.ingest_pipeline import duplicate_response, process_inbound_alert
from app.services.webhook_ingest_audit import serialize_webhook_payload

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"])


@router.post("/v1/ingest")
async def ingest_alert(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    body = await request.json()
    source = client_ip(request)
    logger.info(
        "desktop ingest received user_id=%s source_ip=%s payload=%s",
        user.id,
        source or "-",
        serialize_webhook_payload(body),
    )
    async with ingest_processing_slot(user.id):
        try:
            result = await process_inbound_alert(db, user, body)
        except IntegrityError:
            db.rollback()
            result = duplicate_response(db, user, body)

    logger.info(
        "desktop ingest completed user_id=%s status=%s alert_id=%s",
        user.id,
        result.get("status"),
        result.get("alert_id"),
    )
    return result
